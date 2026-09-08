"""
tests/test_paper_replay.py - task 27.5, ``paper_replay.replay``.

Spec: marketplace-subscriptions-paper-trading task 27.5. ``design.md`` -> "Deterministic replay
(Requirement 15.4, 15.5, P-31)". Requirements 15.4, 15.5, 16.12, 18.1, 18.3, 18.11, 21.2, 21.5,
28.3.

WHAT THIS MODULE ASSERTS
------------------------
1. A recorded session replays to the SAME final order states, fills, balances, positions, realized
   PnL and equity series - compared against the rows the original run PERSISTED, field for field,
   as exact ``Decimal`` with zero tolerance (Requirement 15.4). The whole reconstruction, not a
   summary figure.
2. Replaying twice produces the identical reconstruction, so the answer is a function of the
   recorded log and of nothing else.
3. The reconstruction reads no clock. Two ways: every instant it records is shown to be one the log
   carries, and ``paper_replay``'s view of ``datetime`` is replaced by one whose ``now`` and
   ``utcnow`` RAISE - and the reconstruction comes out identical.
4. A session whose recorded configuration is incomplete, or whose market-data log did not read, is
   REFUSED with a named reason and reconstructs nothing.
5. Another tenant's session answers exactly as an unknown one does.
6. A market event whose stored identity does not match its own payload is refused: the stored log
   is then not the log the session processed.
7. A replay WRITES NOTHING. It is an audit of a recorded session, so it must not be able to alter
   the session it is auditing.

THE FIXTURE IS A REAL SESSION, DRIVEN THROUGH THE PRODUCTION LOOP
----------------------------------------------------------------
The original run here is not a hand-assembled set of rows and is not a sequence of direct
``submit_intent`` calls. It is ``paper_session_service.step_session`` - the production per-event
path - driven one bar at a time over a real ``paper_market_feed.open_feed`` handle fed from
``RecordingRedis``, against ``tests/test_paper_repository.FakeSupabase``. That matters for this
test specifically, because:

* the market events are written to ``paper_market_events`` by ``paper_market_feed`` itself, so the
  replay's input is the log production writes rather than one this file invented; and
* the persisted equity series carries the ``REVALUATION`` point ``step_session`` writes after every
  bar as well as the ``FILL`` point ``apply_fill`` writes, so the reconstruction is compared
  against the whole series Requirement 18.11 asks for rather than against half of it.

Every double is one this repository already has: ``FakeSupabase``, ``RecordingRedis``, the harness
of ``tests/test_task_27_session_service.py`` for the loop, and the two stand-ins that file already
writes for the seams Requirements 17.10 and 23.5 make (``_Runtime`` for the DAG runtime,
``_Recorder`` for the Signal_Trace).

THE ONE THING THE FIXTURE ADDS, AND WHY IT IS NOT A CHEAT
--------------------------------------------------------
``FakeSupabase._defaults`` stamps every inserted row with one shared ``created_at``, and the
interleave of intents and events is recovered from ``paper_orders.created_at`` against
``paper_market_events.received_at``. PostgreSQL's ``NOW()`` is the transaction start time and every
statement here is its own transaction, so a real database stamps distinct, increasing instants.
:class:`_Stamp` reproduces that through the double's existing ``before_insert`` seam, and
:class:`_Clock` advances the feed's pinned instant by one bar interval per bar for the same reason.
Both make the fixture MORE faithful to PostgreSQL than the double's flat default, not less - and
neither touches a price, a quantity or a rate.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

import pytest

from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_replay as replay
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from tests.test_paper_market_feed_selection import _frame
from tests.test_paper_repository import FakeSupabase
from tests.test_task_27_session_service import (
    CAPITAL_MAJOR,
    OTHER_USER,
    SESSION,
    SYMBOL,
    USER,
    _loop_config,
    _loop_double,
    _loop_feed,
    _Recorder,
    _Runtime,
    _signal,
    _step,
)

#: The market instant the first recorded bar opened at. Every later bar is one interval on.
FIRST_BAR = datetime(2025, 6, 15, 9, 0, 0, tzinfo=timezone.utc)
BAR_INTERVAL = timedelta(minutes=1)

#: How long after a bar's market instant the session recorded receiving it. Fixed, so
#: ``latency_ms`` is one exact figure for every bar rather than a range - this file reads no clock
#: either.
DELIVERY_LAG = timedelta(milliseconds=500)

#: A distinct ``signal_id`` per intent, so ``signal_intent_idempotency_key`` gives each order its
#: own key. Two signals sharing one would reach ``paper_simulator``'s idempotency probe and the
#: second would return the first order unchanged - correct behaviour, and not what this fixture is
#: about.
SIGNAL_IDS: Tuple[str, ...] = tuple(
    f"aaaaaaaa-0000-4000-8000-0000000000{index:02d}" for index in range(1, 9)
)


# ══════════════════════════════════════════════════════════════════════════
#  THE TWO THINGS THE DOUBLE DOES NOT MODEL: A MOVING CLOCK AND A MOVING NOW()
# ══════════════════════════════════════════════════════════════════════════


class _Clock:
    """The instant ``paper_market_feed`` reads as "now", advanced one bar at a time.

    ``next_validated_event`` stamps ``paper_market_events.received_at`` and Requirement 14.10's
    ``latency_ms`` from it. A constant would give all five recorded events the same arrival instant
    and the recorded interleave of intents and events would be unrecoverable - which is a property
    of the fixture, not of the log a real session writes.
    """

    def __init__(self) -> None:
        self.at = FIRST_BAR + DELIVERY_LAG

    def __call__(self) -> datetime:
        return self.at


class _Stamp:
    """A ``before_insert`` hook giving each ``paper_orders`` row a distinct ``created_at``.

    See the module docstring for why. It touches ``paper_orders`` only, and only the two timestamp
    columns 009 declares a ``NOW()`` default for.
    """

    def __init__(self) -> None:
        self.at = FIRST_BAR
        self.step = timedelta(seconds=1)

    def __call__(self, client: Any, query: Any) -> None:
        if query.table_name != repo.ORDERS_TABLE or query.payload is None:
            return
        query.payload.setdefault("created_at", self.at.isoformat())
        query.payload.setdefault("updated_at", self.at.isoformat())
        self.at = self.at + self.step


@pytest.fixture(autouse=True)
def _pinned_feed_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Clock]:
    """Pin the feed's clock for every case in this module, and hand the fixture the handle.

    The same pinning ``tests/test_task_27_session_service._LoopCase`` performs, with one
    difference stated in the module docstring: it advances, because five bars a minute apart did
    not all arrive at the same instant.
    """
    clock = _Clock()
    monkeypatch.setattr(feed, "_utc_now", clock)
    yield clock


# ══════════════════════════════════════════════════════════════════════════
#  THE FIXTURE PROGRAMME
# ══════════════════════════════════════════════════════════════════════════


def _bar(
    index: int, *, open_: str, high: str, low: str, close: str, volume: str
) -> Dict[str, Any]:
    """One ``mds:data:*`` frame for bar ``index``, through the shared :func:`_frame` builder.

    The five values are exact decimal **strings** on the wire, which ``decode_payload``'s
    ``parse_float=Decimal`` reads exactly - a ``float`` would be refused downstream
    (Requirement 18.1).
    """
    at = FIRST_BAR + BAR_INTERVAL * index
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return _frame(
        timestamp_ms=int((at - epoch) // timedelta(milliseconds=1)),
        overrides={
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
    )


#: The five bars the fixture session consumes, one minute apart and in ascending order - the feed
#: drops an out-of-order arrival, so the order is part of the premise.
#:
#: Bar 4 is the one whose low reaches the resting buy's limit, and its volume of 1 against the
#: session's recorded ``participation_rate`` of 0.10 caps the fill at 0.1 - exactly half the
#: resting order's 0.2, so the order is left PARTIALLY_FILLED. Bar 5's volume is ample, so the
#: remainder fills and the order reaches its quantity EXACTLY (Requirement 16.14).
FRAMES: Tuple[Dict[str, Any], ...] = (
    _bar(0, open_="59900", high="60100", low="59800", close="60000", volume="10"),
    _bar(1, open_="60000", high="61100", low="59900", close="61000", volume="10"),
    _bar(2, open_="61000", high="62100", low="60900", close="62000", volume="10"),
    _bar(3, open_="61000", high="62000", low="59000", close="59900", volume="1"),
    _bar(4, open_="59900", high="60000", low="59000", close="59500", volume="1000"),
)

#: The market instant of each bar, read back off the frames so the two cannot drift apart.
BAR_INSTANTS: Tuple[datetime, ...] = tuple(
    datetime.fromtimestamp(
        json.loads(frame["data"])["timestamp"] / 1000, tz=timezone.utc
    )
    for frame in FRAMES
)


def _batches() -> Tuple[Any, ...]:
    """What the strategy runtime produced on each of the five bars.

    Bar 1  a market **BUY** of 0.5: a long opens.
    Bar 2  a market **BUY** of 0.25 at a higher close: the position grows and its weighted-average
           entry price moves (Requirement 18.8's one cost-basis convention).
    Bar 3  a market **SELL** of 0.25 - a partial close, which records realized PnL and writes NO
           ``paper_trades`` row because the position has not reached zero (Requirement 18.10);
           a **limit BUY** of 0.2 at 60000, which rests and locks Requirement 16.6's required
           funds; a **limit BUY** at 1, which no bar can reach, so it rests to the very end and
           ``locked_balance`` is strictly positive in the comparison; and a **BUY of 2000**, which
           is above the venue's recorded maximum and is persisted ``REJECTED``
           ``QUANTITY_ABOVE_MAX`` with no balance movement (Requirement 16.5).
    Bar 4  nothing new: the resting buy is capped at exactly half its quantity.
    Bar 5  nothing new: the remainder fills exactly and the order reaches ``FILLED``.
    """
    return (
        (_signal(signal_id=SIGNAL_IDS[0], decision="BUY", side="buy", quantity="0.5"),),
        (_signal(signal_id=SIGNAL_IDS[1], decision="BUY", side="buy", quantity="0.25"),),
        (
            _signal(
                signal_id=SIGNAL_IDS[2], decision="SELL", side="sell", quantity="0.25"
            ),
            _signal(
                signal_id=SIGNAL_IDS[3],
                decision="BUY",
                side="buy",
                quantity="0.2",
                order_type="limit",
                limit_price="60000",
            ),
            _signal(
                signal_id=SIGNAL_IDS[4],
                decision="BUY",
                side="buy",
                quantity="0.1",
                order_type="limit",
                limit_price="1",
            ),
            _signal(
                signal_id=SIGNAL_IDS[5], decision="BUY", side="buy", quantity="2000"
            ),
        ),
        (),
        (),
    )


def _run_the_fixture_session(
    clock: _Clock,
) -> Tuple[FakeSupabase, sim.SessionConfig, str]:
    """Drive one real session over :data:`FRAMES` through ``step_session``, bar by bar.

    One ``step_session`` call per bar, which is the production per-event path: emit the tick, step
    the runtime, record and broadcast each signal, submit its intent, check the resting book, then
    revalue and snapshot the equity. Every row the replay later reads - the ``paper_market_events``
    log included - is written by production code on this path.
    """
    config = _loop_config()
    supabase, session, account_id = _loop_double(capital=CAPITAL_MAJOR)
    supabase.before_insert = _Stamp()
    handle, _ = _loop_feed(supabase, list(FRAMES))
    runtime = _Runtime(*_batches())
    # The Signal_Trace recorder is a SEAM (Requirement 23.5), so the loop is given the stand-in
    # ``tests/test_task_27_session_service.py`` already writes for it. Omitting it would leave the
    # loop logging that no recorder is installed on every bar, which is a real defect elsewhere and
    # noise here.
    recorder = _Recorder(supabase)

    for index, instant in enumerate(BAR_INSTANTS):
        # The bar arrived half a second after it opened, and the orders it caused were inserted in
        # the seconds after that - which is the interleave the replay recovers from the log.
        clock.at = instant + DELIVERY_LAG
        supabase.before_insert.at = instant + DELIVERY_LAG + timedelta(seconds=1)
        step = _step(
            supabase,
            session,
            handle,
            account_id=account_id,
            config=config,
            evaluate=runtime,
            plan=f"plan-{index}",
            record_signal=recorder,
        )
        assert step.event is not None, (
            f"bar {index} was dropped by the feed, so the fixture never recorded it; "
            f"frame={FRAMES[index]!r}"
        )
    return (supabase, config, account_id)


# ══════════════════════════════════════════════════════════════════════════
#  THE TWO PROJECTIONS THE COMPARISON IS OVER
# ══════════════════════════════════════════════════════════════════════════


def _numeric(value: Any) -> Decimal:
    """One ``NUMERIC(28,10)`` column as an exact ``Decimal``. Never a ``float``."""
    return Decimal(str(value))


def _optional_numeric(value: Any) -> Optional[Decimal]:
    return None if value is None else _numeric(value)


def _instant(value: Any) -> Optional[datetime]:
    """One ``TIMESTAMPTZ`` column as a tz-aware UTC ``datetime``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _position_rank(row: Mapping[str, Any]) -> Tuple[int, str, int]:
    """How recent a ``paper_positions`` row is - an OPEN row outranks every closed one."""
    return (
        0 if row.get("closed_at") else 1,
        str(row.get("closed_at") or ""),
        int(row.get("version") or 1),
    )


def _stored(supabase: FakeSupabase) -> Dict[str, Any]:
    """Everything the comparison is over, read out of the rows the ORIGINAL run persisted.

    Read from the rows and never from a returned ``SessionStep``, ``SubmitOutcome`` or
    ``FillOutcome``: the claim is about what the session recorded, and an object the code under
    test handed back is that code's own account of itself.
    """
    assert len(supabase.accounts) == 1, (
        f"the session should hold exactly one Paper_Account, found {len(supabase.accounts)}"
    )
    account = supabase.accounts[0]

    positions: Dict[str, Dict[str, Any]] = {}
    for row in supabase.positions:
        symbol = str(row["symbol"])
        current = positions.get(symbol)
        if current is None or _position_rank(row) > _position_rank(current):
            positions[symbol] = dict(row)

    return {
        "balances": {
            "available_balance": _numeric(account["available_balance"]),
            "locked_balance": _numeric(account["locked_balance"]),
            "total_equity": _numeric(account["total_equity"]),
        },
        "realized_pnl": _numeric(account["realized_pnl"]),
        "orders": {
            str(row["id"]): {
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "order_type": str(row["order_type"]),
                "quantity": _numeric(row["quantity"]),
                "limit_price": _optional_numeric(row.get("limit_price")),
                "reference_price": _optional_numeric(row.get("reference_price")),
                "filled_quantity": _numeric(row["filled_quantity"]),
                "avg_fill_price": _optional_numeric(row.get("avg_fill_price")),
                "fee_minor": int(row.get("fee_minor") or 0),
                "slippage_minor": int(row.get("slippage_minor") or 0),
                "order_state": str(row["order_state"]),
                "rejection_reason": (
                    None
                    if row.get("rejection_reason") is None
                    else str(row["rejection_reason"])
                ),
            }
            for row in supabase.orders
        },
        "fills": {
            (str(row["order_id"]), str(row["fill_event_id"])): {
                "quantity": _numeric(row["quantity"]),
                "price": _numeric(row["price"]),
                "fee_minor": int(row["fee_minor"]),
                "slippage_minor": int(row["slippage_minor"]),
                "filled_at": _instant(row["filled_at"]),
            }
            for row in supabase.fills
        },
        "positions": {
            symbol: {
                "side": str(row["side"]),
                "size": _numeric(row["size"]),
                "entry_price": _numeric(row["entry_price"]),
                "current_price": _optional_numeric(row.get("current_price")),
                "unrealized_pnl": _optional_numeric(row.get("unrealized_pnl")),
                "price_at": _instant(row.get("price_at")),
                "opened_at": _instant(row.get("opened_at")),
                "closed_at": _instant(row.get("closed_at")),
            }
            for symbol, row in positions.items()
        },
        "closed_trades": [
            {
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "quantity": _numeric(row["quantity"]),
                "entry_price": _numeric(row["entry_price"]),
                "exit_price": _numeric(row["exit_price"]),
                "realized_pnl": _numeric(row["realized_pnl"]),
            }
            for row in supabase.trades
        ],
        "equity_series": [
            {
                "cause": str(row["cause"]),
                "total_equity": _numeric(row["total_equity"]),
                "available_balance": _numeric(row["available_balance"]),
                "locked_balance": _numeric(row["locked_balance"]),
                "position_market_value": _numeric(row["position_market_value"]),
                "taken_at": _instant(row["taken_at"]),
            }
            for row in supabase.equity_snapshots
        ],
    }


def _reconstructed(result: replay.Reconstruction) -> Dict[str, Any]:
    """The same figures off the reconstruction, in the same shape.

    ``series_index`` and ``stale`` are not compared, and neither is a gap:

    * the persisted ``series_index`` is the SESSION's equity-series number - Requirement 17.15's
      reset restarts it - while the ledger's is the ordinal of the point within the series it
      built. Two different measurements under one name.
    * the persisted ``paper_equity_snapshots.stale`` is a per-row flag the loop writes from the
      symbols the bar supplied a price for; the reconstruction reports one session-level ``stale``.
      It is asserted directly in
      :func:`test_a_single_symbol_session_reports_no_stale_valuation` instead.

    The reconstruction's ``SESSION_START`` point has no persisted counterpart: ``start_session``
    writes the opening snapshot and this fixture's premise is the account
    ``get_or_create_account`` leaves behind, so the series the loop wrote begins at its first
    write. It is dropped here and asserted on its own terms below.
    """
    return {
        "balances": dict(result.balances),
        "realized_pnl": result.realized_pnl,
        "orders": {
            order.order_id: {
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "limit_price": order.limit_price,
                "reference_price": order.reference_price,
                "filled_quantity": order.filled_quantity,
                "avg_fill_price": order.avg_fill_price,
                "fee_minor": order.fee_minor,
                "slippage_minor": order.slippage_minor,
                "order_state": order.order_state,
                "rejection_reason": order.rejection_reason,
            }
            for order in result.orders
        },
        "fills": {
            (fill.order_id, fill.fill_event_id): {
                "quantity": fill.quantity,
                "price": fill.price,
                "fee_minor": fill.fee_minor,
                "slippage_minor": fill.slippage_minor,
                "filled_at": fill.filled_at,
            }
            for fill in result.fills
        },
        "positions": {
            symbol: {
                "side": position["side"],
                "size": position["size"],
                "entry_price": position["entry_price"],
                "current_price": position["current_price"],
                "unrealized_pnl": position["unrealized_pnl"],
                "price_at": position["price_at"],
                "opened_at": position["opened_at"],
                "closed_at": position["closed_at"],
            }
            for symbol, position in result.positions.items()
        },
        "closed_trades": [
            {
                "symbol": trade["symbol"],
                "side": trade["side"],
                "quantity": trade["quantity"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "realized_pnl": trade["realized_pnl"],
            }
            for trade in result.closed_trades
        ],
        "equity_series": [
            {
                "cause": point["cause"],
                "total_equity": point["total_equity"],
                "available_balance": point["available_balance"],
                "locked_balance": point["locked_balance"],
                "position_market_value": point["position_market_value"],
                "taken_at": point["taken_at"],
            }
            for point in result.equity_series
            if point["cause"] != replay.SESSION_START
        ],
    }


def _writes(supabase: FakeSupabase, mark: int) -> List[Tuple[str, str]]:
    """The ``(table, op)`` of every write issued after ``mark``."""
    return [
        (q.table_name, q.op)
        for q in supabase.statements[mark:]
        if q.op in ("insert", "update")
    ]


# ══════════════════════════════════════════════════════════════════════════
#  1 - THE RECONSTRUCTION IS THE RECORDED SESSION (Requirement 15.4)
# ══════════════════════════════════════════════════════════════════════════


def test_a_replay_reproduces_the_recorded_session_exactly(_pinned_feed_clock: _Clock) -> None:
    """Requirement 15.4, on the whole reconstruction rather than on a summary figure.

    The final order states, fills, balances, positions, realized PnL, closed round trips and the
    equity series all equal the ones the original run PERSISTED, as exact ``Decimal`` with zero
    tolerance. Compared subject by subject so a failure names which one diverged.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    stored = _stored(supabase)

    result = replay.replay(supabase, USER, SESSION)
    assert result is not None
    subject = _reconstructed(result)

    assert subject["balances"] == stored["balances"], (
        "the replay and the recorded session disagree about the BALANCES:\n"
        f"  replay:   {subject['balances']}\n"
        f"  recorded: {stored['balances']}"
    )
    assert subject["realized_pnl"] == stored["realized_pnl"], (
        "the replay and the recorded session disagree about REALIZED PROFIT AND LOSS: "
        f"{subject['realized_pnl']} vs {stored['realized_pnl']}"
    )
    assert set(subject["orders"]) == set(stored["orders"]), (
        "the replay reconstructed a different set of ORDERS than the session recorded:\n"
        f"  replay:   {sorted(subject['orders'])}\n"
        f"  recorded: {sorted(stored['orders'])}"
    )
    for order_id in sorted(stored["orders"]):
        assert subject["orders"][order_id] == stored["orders"][order_id], (
            f"the replay and the recorded session disagree about ORDER {order_id}:\n"
            f"  replay:   {subject['orders'][order_id]}\n"
            f"  recorded: {stored['orders'][order_id]}"
        )
    assert set(subject["fills"]) == set(stored["fills"]), (
        "the replay reconstructed a different set of FILLS. The fill_event_id is derived from the "
        "order (a market fill) or from the order and the event identity (a resting fill), so a "
        "difference here is a difference about which event filled which order:\n"
        f"  replay:   {sorted(subject['fills'])}\n"
        f"  recorded: {sorted(stored['fills'])}"
    )
    for key in sorted(stored["fills"]):
        assert subject["fills"][key] == stored["fills"][key], (
            f"the replay and the recorded session disagree about FILL {key}:\n"
            f"  replay:   {subject['fills'][key]}\n"
            f"  recorded: {stored['fills'][key]}"
        )
    assert subject["positions"] == stored["positions"], (
        "the replay and the recorded session disagree about the POSITIONS:\n"
        f"  replay:   {subject['positions']}\n"
        f"  recorded: {stored['positions']}"
    )
    assert subject["closed_trades"] == stored["closed_trades"], (
        "the replay and the recorded session disagree about the CLOSED ROUND TRIPS:\n"
        f"  replay:   {subject['closed_trades']}\n"
        f"  recorded: {stored['closed_trades']}"
    )
    assert len(subject["equity_series"]) == len(stored["equity_series"]), (
        "the replay and the recorded session disagree about the LENGTH of the equity series. "
        "Requirement 18.11 wants one point per applied fill and one per revaluation from a "
        "validated price:\n"
        f"  replay:   {[p['cause'] for p in subject['equity_series']]}\n"
        f"  recorded: {[p['cause'] for p in stored['equity_series']]}"
    )
    for index, (mine, theirs) in enumerate(
        zip(subject["equity_series"], stored["equity_series"])
    ):
        assert mine == theirs, (
            f"the replay and the recorded session disagree about EQUITY POINT {index}:\n"
            f"  replay:   {mine}\n"
            f"  recorded: {theirs}"
        )


def test_the_fixture_session_actually_forced_every_case_the_comparison_is_about(
    _pinned_feed_clock: _Clock,
) -> None:
    """The premise of the test above, asserted rather than assumed.

    A programme that quietly stopped filling would make every comparison in
    :func:`test_a_replay_reproduces_the_recorded_session_exactly` a claim about two empty
    structures. So the recorded rows are counted: three market fills, a resting order filled in two
    capped parts that reached FILLED exactly, a resting order left ACCEPTED with funds locked, a
    persisted rejection, a partial close that moved realized PnL without writing a trade row, and a
    non-zero fee and slippage on one fill.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    stored = _stored(supabase)
    orders = stored["orders"]

    states = sorted(order["order_state"] for order in orders.values())
    assert states == ["ACCEPTED", "FILLED", "FILLED", "FILLED", "FILLED", "REJECTED"], (
        f"the fixture recorded the order states {states}, which is not the programme this file "
        f"documents"
    )
    rejected = [o for o in orders.values() if o["order_state"] == "REJECTED"]
    assert rejected[0]["rejection_reason"] == sim.REJECTION_QUANTITY_ABOVE_MAX
    assert rejected[0]["filled_quantity"] == Decimal("0.0000000000")

    resting = [
        o
        for o in orders.values()
        if o["order_type"] == "limit" and o["order_state"] == "FILLED"
    ]
    assert len(resting) == 1
    assert resting[0]["filled_quantity"] == resting[0]["quantity"], (
        "Requirement 16.14: FILLED if and only if the filled quantity EQUALS the ordered quantity"
    )
    resting_fills = [
        key
        for key in stored["fills"]
        if orders[key[0]]["order_type"] == "limit"
    ]
    assert len(resting_fills) == 2, (
        f"the resting order should have filled in two capped parts, found {len(resting_fills)}"
    )

    assert stored["balances"]["locked_balance"] > Decimal("0"), (
        "the untriggerable resting order should leave locked_balance strictly positive"
    )
    assert stored["closed_trades"] == [], (
        "the fixture's partial close must write no paper_trades row (Requirement 18.10)"
    )
    assert sorted(stored["positions"]) == [SYMBOL]
    assert stored["positions"][SYMBOL]["size"] > Decimal("0")
    assert stored["realized_pnl"] != Decimal("0"), "the partial close should move realized PnL"

    causes = [point["cause"] for point in stored["equity_series"]]
    assert causes.count("FILL") == len(stored["fills"])
    assert causes.count("REVALUATION") > 0, (
        "the loop wrote no REVALUATION point, so the reconstruction's revaluation is compared "
        "against nothing"
    )

    both_costs = [
        row
        for row in supabase.fills
        if int(row["fee_minor"]) > 0 and int(row["slippage_minor"]) > 0
    ]
    assert both_costs, (
        "no recorded fill carried both a non-zero fee and a non-zero slippage, so the "
        "reconstruction's cost columns are compared against zeros"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2 - DETERMINISM, AND 3 - NO CLOCK (Requirement 15.4)
# ══════════════════════════════════════════════════════════════════════════


def test_replaying_twice_produces_the_identical_reconstruction(
    _pinned_feed_clock: _Clock,
) -> None:
    """The answer is a function of the recorded log and of nothing else.

    Compared through :meth:`Reconstruction.as_dict`, so the whole reconstruction is the subject -
    every order, every fill, every position, every equity point and every timestamp.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)

    first = replay.replay(supabase, USER, SESSION)
    second = replay.replay(supabase, USER, SESSION)

    assert first is not None and second is not None
    assert first.as_dict() == second.as_dict(), (
        "two replays of one recorded log produced different reconstructions, so something on the "
        "path is not a function of the log"
    )


def test_the_reconstruction_records_only_instants_the_log_carries(
    _pinned_feed_clock: _Clock,
) -> None:
    """Every timestamp written comes from a recorded event, never from ``now()``.

    The positive form of the no-clock claim: the set of instants the reconstruction contains is a
    subset of the instants the log contains. A ``now()`` anywhere on the path would put an instant
    in the reconstruction that is in neither set, whatever else it left unchanged.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    result = replay.replay(supabase, USER, SESSION)
    assert result is not None

    bar_instants = set(BAR_INSTANTS)
    order_instants = {_instant(row["created_at"]) for row in supabase.orders}
    session_start = _instant(supabase.sessions[0]["started_at"])
    permitted = bar_instants | {session_start, None}

    for fill in result.fills:
        assert fill.filled_at in bar_instants, (
            f"fill {fill.fill_event_id} was recorded at {fill.filled_at}, which is not any "
            f"recorded bar's market instant"
        )
    for order in result.orders:
        assert order.created_at in order_instants, (
            f"order {order.order_id} reports {order.created_at}, which is not its recorded "
            f"created_at"
        )
    for point in result.equity_series:
        assert point["taken_at"] in permitted, (
            f"an equity point was taken at {point['taken_at']}, which is neither a recorded bar "
            f"instant nor the session's recorded start"
        )
    for symbol, position in result.positions.items():
        assert position["price_at"] in permitted, (
            f"{symbol}'s position was priced at {position['price_at']}, which no recorded event "
            f"supplies"
        )
        assert position["opened_at"] in permitted


def test_the_reconstruction_is_unchanged_when_every_clock_read_raises(
    _pinned_feed_clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative form: replace the module's ``datetime`` with one whose clock refuses.

    ``paper_replay`` reads its instants through the module-global ``datetime``, so a stand-in whose
    ``now``, ``utcnow``, ``today`` and ``fromtimestamp`` raise turns any clock read on the path into
    a failure rather than into a plausible value. It is a real ``datetime`` subclass, so
    ``fromisoformat`` and every comparison keep working - which is the point: the reconstruction has
    to come out *identical*, not merely to avoid crashing.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    expected = replay.replay(supabase, USER, SESSION)
    assert expected is not None

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "paper_replay read a clock. Requirement 15.4's byte-identical replay requires every "
            "instant it records to come from the recorded event, not from now()."
        )

    guarded = type(
        "_ClocklessDatetime",
        (datetime,),
        {
            "now": classmethod(_refuse),
            "utcnow": classmethod(_refuse),
            "today": classmethod(_refuse),
            "fromtimestamp": classmethod(_refuse),
        },
    )
    monkeypatch.setattr(replay, "datetime", guarded)

    result = replay.replay(supabase, USER, SESSION)
    assert result is not None
    assert result.as_dict() == expected.as_dict(), (
        "the reconstruction changed when the module's clock was taken away, so some figure in it "
        "was coming from a clock rather than from the log"
    )


def test_a_replay_writes_nothing(_pinned_feed_clock: _Clock) -> None:
    """An audit must not be able to alter the session it is auditing.

    The reconstruction lives in the in-memory ledger, so the statements a replay issues are reads
    only - and every recorded row is byte-identical before and after.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    names = (
        "accounts",
        "orders",
        "fills",
        "positions",
        "trades",
        "equity_snapshots",
        "balance_events",
        "market_events",
        "events",
        "sessions",
    )
    before = copy.deepcopy({name: getattr(supabase, name) for name in names})
    mark = len(supabase.statements)

    assert replay.replay(supabase, USER, SESSION) is not None

    assert _writes(supabase, mark) == [], (
        f"a replay issued {_writes(supabase, mark)}; it must read only"
    )
    assert before == {name: getattr(supabase, name) for name in names}


# ══════════════════════════════════════════════════════════════════════════
#  4 - THE REFUSALS (Requirements 16.12, 28.3)
# ══════════════════════════════════════════════════════════════════════════


def test_an_incomplete_recorded_configuration_is_refused_and_reconstructs_nothing(
    _pinned_feed_clock: _Clock,
) -> None:
    """A session's configuration is read as it was written or not at all.

    Requirement 16.12 captures the configuration at start and keeps it unchanged for the session's
    lifetime, and Requirement 28.3 forbids substituting a figure nobody recorded. A payload missing
    ``fee_rate`` therefore refuses rather than replaying every recorded fill at a zero fee.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    stripped = dict(supabase.sessions[0]["config"])
    del stripped["fee_rate"]
    supabase.sessions[0]["config"] = stripped

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_CONFIG_INCOMPLETE
    assert raised.value.session_id == SESSION
    assert "fee_rate" in str(raised.value)


def test_an_unreadable_market_event_log_is_refused_and_reconstructs_nothing(
    _pinned_feed_clock: _Clock,
) -> None:
    """A read that DID NOT COMPLETE is not a read that found nothing.

    Answering with an empty event log would report a session that never traded, which is the
    fabricated measurement Requirement 28.3 forbids. So the statement's failure becomes a named
    refusal and no reconstruction is produced.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    supabase.raise_on.add(("select", repo.MARKET_EVENTS_TABLE))

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_MARKET_LOG_UNREADABLE


def test_an_unreadable_order_log_is_refused_and_reconstructs_nothing(
    _pinned_feed_clock: _Clock,
) -> None:
    """The same disposition for the other half of the input: the recorded order intents."""
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    supabase.raise_on.add(("select", repo.ORDERS_TABLE))

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_ORDER_LOG_UNREADABLE


def test_an_unreadable_session_read_is_refused_rather_than_answered_as_absent(
    _pinned_feed_clock: _Clock,
) -> None:
    """``None`` means "the read completed and matched nothing" and nothing else."""
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    supabase.raise_on.add(("select", repo.SESSIONS_TABLE))

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_SESSION_UNREADABLE


def test_an_unreadable_initial_capital_is_refused(_pinned_feed_clock: _Clock) -> None:
    """The balance every figure is measured against is recorded, or the replay refuses.

    A zero would be a fabricated starting balance and would make every equity point and every
    return percentage below it a measurement of nothing (Requirement 28.3).
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    supabase.sessions[0]["initial_capital_minor"] = None

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_CAPITAL_UNREADABLE


def test_a_market_event_whose_identity_does_not_match_its_payload_is_refused(
    _pinned_feed_clock: _Clock,
) -> None:
    """The stored log has to be the log the session processed.

    ``paper_market_feed.source_event_id`` is public precisely so an audit can RECOMPUTE an identity
    from a stored row rather than trust the stored string. A payload whose close has been altered
    computes a different identity, which means the row is not the event the session priced from -
    so it is refused rather than replayed.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    row = supabase.market_events[0]
    row["payload"] = dict(row["payload"], close="99999")

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_EVENT_IDENTITY_MISMATCH


def test_a_market_event_with_no_readable_instant_is_refused(
    _pinned_feed_clock: _Clock,
) -> None:
    """Every instant the replay records comes from the event, so an event without one refuses.

    No clock is read to stand in for it, which is the whole of Requirement 15.4's determinism.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    supabase.market_events[0]["event_timestamp"] = "not-an-instant"

    with pytest.raises(replay.ReplayRefused) as raised:
        replay.replay(supabase, USER, SESSION)

    assert raised.value.reason == replay.REFUSAL_EVENT_UNREADABLE


def test_a_payload_that_cannot_be_verified_is_counted_rather_than_refused(
    _pinned_feed_clock: _Clock,
) -> None:
    """A row whose payload does not carry the identity fields is unverifiable, not wrong.

    The distinction matters: the pricing fields such a row does carry are still the ones the
    session priced from, so refusing it would be a refusal to audit rather than an audit. It is
    counted in :attr:`Reconstruction.events_unverified` so the gap is reported rather than assumed
    away, and the reconstruction is otherwise identical - the identity is not a pricing input.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    verified = replay.replay(supabase, USER, SESSION)
    assert verified is not None
    assert verified.events_verified == len(FRAMES)
    assert verified.events_unverified == 0

    stripped = dict(supabase.market_events[0]["payload"])
    del stripped["exchange"]
    supabase.market_events[0]["payload"] = stripped

    result = replay.replay(supabase, USER, SESSION)
    assert result is not None
    assert result.events_verified == len(FRAMES) - 1
    assert result.events_unverified == 1
    assert _reconstructed(result) == _reconstructed(verified)


# ══════════════════════════════════════════════════════════════════════════
#  5 - TENANCY (Requirements 21.2, 21.5)
# ══════════════════════════════════════════════════════════════════════════


def test_another_tenants_session_answers_exactly_as_an_unknown_one_does(
    _pinned_feed_clock: _Clock,
) -> None:
    """One answer for "no such session" and "not yours", and it discloses neither.

    A distinct answer for the second would tell the caller that the session exists, which is what
    Requirements 21.2 and 21.5 forbid - and it is why ``read_session_lifecycle`` gives them the
    same answer too.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    unknown_id = "44444444-4444-4444-8444-444444444444"

    other_tenant = replay.replay(supabase, OTHER_USER, SESSION)
    unknown = replay.replay(supabase, OTHER_USER, unknown_id)
    unknown_to_owner = replay.replay(supabase, USER, unknown_id)

    assert other_tenant is None
    assert unknown is None
    assert unknown_to_owner is None


def test_every_read_the_replay_issues_carries_user_id_as_a_predicate(
    _pinned_feed_clock: _Clock,
) -> None:
    """No statement on this path is scoped by anything weaker than the caller's identity.

    Requirements 21.2, 21.5 and 24.9: ``user_id`` is a PREDICATE on the statement, not a filter
    applied to rows that were already fetched. Asserted against the statements the double recorded
    rather than against the repository's docstrings.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    mark = len(supabase.statements)

    assert replay.replay(supabase, USER, SESSION) is not None

    # ``paper_accounts`` is excluded: the only statement against it here is
    # ``paper_persistence_supported``'s existence probe, which reads no row's contents.
    scoped = [
        q
        for q in supabase.statements[mark:]
        if q.table_name.startswith("paper_") and q.table_name != repo.ACCOUNTS_TABLE
    ]
    assert scoped, "the replay issued no scoped statement at all, so this asserts nothing"
    for query in scoped:
        assert "user_id" in query.filtered_columns(), (
            f"the replay's {query.op} on {query.table_name} does not carry user_id as a "
            f"predicate: {query.filters}"
        )
        assert query.filter_value("user_id") == USER


# ══════════════════════════════════════════════════════════════════════════
#  6 - THE SERIES, AND THE EMPTY LOG
# ══════════════════════════════════════════════════════════════════════════


def test_every_equity_point_the_replay_records_satisfies_the_equity_identity(
    _pinned_feed_clock: _Clock,
) -> None:
    """Requirement 18.3, on every point of the reconstructed series, with zero tolerance."""
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    result = replay.replay(supabase, USER, SESSION)
    assert result is not None

    causes = [point["cause"] for point in result.equity_series]
    assert causes[0] == replay.SESSION_START, (
        f"the reconstructed series should open on the session's start point, got {causes[:1]}"
    )
    assert causes.count(replay.FILL) == len(result.fills), (
        "Requirement 18.11 wants exactly one equity point per applied fill; the series holds "
        f"{causes.count(replay.FILL)} for {len(result.fills)} fill(s)"
    )
    for index, point in enumerate(result.equity_series):
        assert point["total_equity"] == (
            point["available_balance"]
            + point["locked_balance"]
            + point["position_market_value"]
        ), (
            f"equity point {index} ({point['cause']}) breaks Requirement 18.3's identity: {point}"
        )
    assert result.equity_series[-1]["total_equity"] == result.balances["total_equity"]
    assert result.equity_series[0]["taken_at"] == _instant(
        supabase.sessions[0]["started_at"]
    )


def test_a_single_symbol_session_reports_no_stale_valuation(
    _pinned_feed_clock: _Clock,
) -> None:
    """A session whose every bar prices its one open position is not stale (Requirement 18.15).

    The flag is a measurement rather than a default, so it is asserted rather than skipped: this
    session trades one symbol and every bar carries a close for it, so nothing fell back to a last
    validated price and nothing was interpolated.
    """
    supabase, _, _ = _run_the_fixture_session(_pinned_feed_clock)
    result = replay.replay(supabase, USER, SESSION)
    assert result is not None

    assert result.stale is False
    assert [str(row["stale"]) for row in supabase.equity_snapshots] == [
        str(False) for _ in supabase.equity_snapshots
    ]


def test_a_session_with_no_recorded_event_reconstructs_its_opening_state() -> None:
    """An empty market-data log is a real answer, and it is not a refusal.

    ``[]`` from ``get_market_events`` means the read completed and the session recorded no event
    yet. That session traded nothing, so the reconstruction is its opening balance and one
    ``SESSION_START`` equity point - which is different from the refusal an unreadable log gets,
    and the difference is the whole of why the two are separate outcomes.
    """
    supabase, _, _ = _loop_double(capital=CAPITAL_MAJOR)

    result = replay.replay(supabase, USER, SESSION)

    assert result is not None
    assert result.events_replayed == 0
    assert result.intents_replayed == 0
    assert result.orders == ()
    assert result.fills == ()
    assert result.positions == {}
    assert result.realized_pnl == Decimal("0.00")
    assert result.balances == {
        "available_balance": CAPITAL_MAJOR.quantize(Decimal("0.01")),
        "locked_balance": Decimal("0.00"),
        "total_equity": CAPITAL_MAJOR.quantize(Decimal("0.01")),
    }
    assert [point["cause"] for point in result.equity_series] == [replay.SESSION_START]


def test_the_reconstruction_reports_what_it_was_built_from(
    _pinned_feed_clock: _Clock,
) -> None:
    """The counts an audit needs, so "it replayed the log" is checkable rather than trusted."""
    supabase, config, _ = _run_the_fixture_session(_pinned_feed_clock)
    result = replay.replay(supabase, USER, SESSION)
    assert result is not None

    assert result.session_id == SESSION
    assert result.user_id == USER
    assert result.session_state == "RUNNING"
    assert result.currency == "USD"
    assert result.initial_balance == CAPITAL_MAJOR.quantize(Decimal("0.01"))
    assert result.events_replayed == len(FRAMES)
    assert result.intents_replayed == len(supabase.orders)
    assert result.config.fee_rate == config.fee_rate
    assert result.config.slippage_rate == config.slippage_rate
    assert result.config.participation_rate == config.participation_rate
    assert result.tied_intents == 0, (
        "the fixture stamps a distinct created_at per order, so no tie should have been broken; "
        f"the replay reports {result.tied_intents}"
    )
    assert set(result.order_states.values()) == {"FILLED", "ACCEPTED", "REJECTED"}
