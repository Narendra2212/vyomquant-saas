"""
tests/test_paper_order_lifecycle_writes.py - tasks 25.3, 25.4, 25.5 and 25.6.

Spec: marketplace-subscriptions-paper-trading tasks 25.3-25.6. ``design.md`` ->
"``paper/paper_simulator.py``". Requirements 14.5, 14.9, 16.3, 16.5, 16.6, 16.7, 16.8, 16.9,
16.10, 16.12, 16.13, 16.14, 16.15, 18.1, 18.4, 18.6, 18.7, 18.11, 18.14.

Conventional regression tests for the four order-lifecycle tasks. The property tests are
25.7-25.15 and are deliberately **not** here - this file asserts named examples and the edge cases
the requirements name, which is what a property test's counterexample is compared against.

WHAT IS ASSERTED, AND WHY EACH CLAIM NEEDED A TEST
--------------------------------------------------
1. **The idempotency probe is inside the attempt and compares fingerprints.** A matching key
   returns the recorded order with no second order and no balance movement (Requirement 16.8); a
   mismatching one raises 409 ``PAPER_IDEMPOTENCY_CONFLICT`` and leaves the recorded order alone
   (Requirement 16.15). Asserted on the row count *and* on the balances, because "no second order"
   and "no balance movement" are two different claims.

2. **All eight Requirement 16.5 rejection reasons.** Four are persisted ``REJECTED`` orders from
   ``CREATED``; four describe an intent the ``paper_orders`` CHECK constraints refuse, so no row
   can exist and the refusal is a 400 carrying the same reason - see
   ``paper_simulator.ORDER_COLUMN_CONSTRAINTS`` for that gap. Both branches are asserted, because
   the Persistence_Layer double does not model those four CHECKs and a test that leaned on it
   would pass here and fail against PostgreSQL.

3. **``NO_VALIDATED_PRICE`` rather than a synthesised one** (Requirement 14.9), and
   **``INSUFFICIENT_FUNDS`` locking nothing** (Requirement 16.6) - the latter asserted on
   ``locked_balance`` and on the absence of any ``ORDER_LOCK`` ledger row.

4. **The fill path's four guards**: a terminal order, a duplicate ``(order_id, fill_event_id)``, an
   over-fill and an invariant breach. Each asserted on *every* table, because "changes nothing"
   is a claim about seven tables and not only about the order.

5. **The feed gate** (Requirement 14.5's simulator half, which task 24.4 left outstanding): no
   fill while ``feed_state != 'HEALTHY'``, and a market order not even accepted then.

6. **The fill model** (task 25.5): a market order slips adversely, a resting limit order fills at
   **exactly** its limit with zero slippage even when the candle traded through it, and the partial
   fill is a participation **cap** rather than a probability.

7. **The retry path** (task 25.6): three attempts, jitter-free bounded backoff, then 409
   ``PAPER_CONCURRENCY_CONFLICT`` - asserted on the delays as well as on the count, because a
   jittered backoff would break Requirement 15.4's replay.

THE PERSISTENCE_LAYER DOUBLE
----------------------------
``tests/test_paper_repository.FakeSupabase``, the one double this repository has. No second double
and no mock of the repository: every statement these tests observe is issued by the same code
production issues it from. ``_run_coroutine`` drives the coroutines on the harness's own loop -
never ``asyncio.run``, which closes the loop it created and breaks any later test in the session
that expected one. That loop is built once per process and reused; see :data:`_HARNESS_LOOP` for why
a loop per coroutine hung the paper property suite on Windows.
"""

from __future__ import annotations

import asyncio
import atexit
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import (
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_IDEMPOTENCY_CONFLICT,
    PAPER_INVARIANT_VIOLATION,
    PAPER_ORDER_INVALID,
    PAPER_OVER_FILL,
)
from backend_app.backend.paper.paper_market_feed import FeedNotHealthy
from backend_app.backend.paper.paper_order_state import PaperOrderState
from tests.test_paper_repository import FakeSupabase

USER = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"
SYMBOL = "BTC/USDT"
EXCHANGE = "binance"

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
CAPITAL = Decimal("100000")


#: The one loop every coroutine this harness drives runs on, built on first use and reused for the
#: rest of the process.
#:
#: ONE LOOP, NOT ONE PER CALL - AND WHY THAT IS NOT AN OPTIMISATION
#: ---------------------------------------------------------------
#: An event loop is not a cheap object on every platform. On Windows the default is
#: ``ProactorEventLoop``, whose ``_make_self_pipe`` calls ``socket.socketpair()``, and Windows has
#: no ``AF_UNIX`` socketpair - CPython falls back to ``socket._fallback_socketpair``, which binds a
#: listener on ``127.0.0.1``, connects to it and accepts. So a loop is **two real loopback TCP
#: connections**, and closing it parks their ephemeral ports in ``TIME_WAIT`` for the system's
#: ``TcpTimedWaitDelay`` - minutes, process-independent, from a range of 16384 ports.
#:
#: The paper property modules that import this harness drive tens of thousands of coroutines in one
#: session (``tests/property/test_paper_idempotence.py`` alone offers a fill event four times over
#: four scenarios for each of a hundred examples). A loop per coroutine therefore consumed the
#: machine's whole ephemeral range faster than ``TIME_WAIT`` released it, and once the range was
#: gone the ``connect``/``accept`` handshake inside ``_fallback_socketpair`` blocked - the session
#: stopped dead with no output and no CPU, which is what "the suite hangs when two of these modules
#: run together" was. One loop for the process makes that two ports for the process.
#:
#: Reuse is safe because ``run_until_complete`` may be called on the same non-running loop any
#: number of times, and because nothing on these paths leaves work behind on the loop: no
#: ``create_task``, no ``ensure_future``, no ``asyncio.Lock`` and no async generator appears in
#: ``paper_simulator``, ``paper_repository`` or ``paper_accounting``, so every drive returns with the
#: loop as idle as it found it. A nested drive was never possible and still is not: asyncio refuses
#: to run one loop inside another, whether or not it is the same one.
_HARNESS_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _harness_loop() -> asyncio.AbstractEventLoop:
    """The harness's loop, created once. Rebuilt only if something has closed it."""
    global _HARNESS_LOOP
    loop = _HARNESS_LOOP
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _HARNESS_LOOP = loop
    return loop


@atexit.register
def _close_the_harness_loop() -> None:
    """Release the loop's two sockets at process exit, so nothing is left for the OS to reap."""
    global _HARNESS_LOOP
    loop, _HARNESS_LOOP = _HARNESS_LOOP, None
    if loop is not None and not loop.is_closed():
        loop.close()


def _run_coroutine(coro: Any) -> Any:
    """Drive one coroutine to completion on the harness's loop.

    ``asyncio.run`` is not used, and never was: it closes the loop it created and tears down the
    async generators bound to it, which breaks any later test in the session that expected a loop to
    still be there - the reason ``tests/test_settlement_service.py`` established this pattern and
    ``tests/test_paper_simulator_config.py`` follows it.

    The loop is the process's one loop rather than a fresh one per call. See :data:`_HARNESS_LOOP`
    for why that is a correctness matter on Windows and not a speed-up.
    """
    return _harness_loop().run_until_complete(coro)


class _Sleeps:
    """A recording stand-in for ``asyncio.sleep``.

    The delays are the assertion, not an incidental. Requirement 15.4 requires a replayed session
    to reproduce its fills, and a jittered backoff would move the retry points between the run and
    the replay - so the test reads the sequence rather than merely counting it.
    """

    def __init__(self) -> None:
        self.delays: List[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


# ══════════════════════════════════════════════════════════════════════════
#  THE PREMISE: A SESSION, ITS ACCOUNT, AND ITS FROZEN CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════


def _market_entry() -> Dict[str, Any]:
    """One CCXT market entry, in the shape ``connection_engine`` produces.

    Precision as a **tick size**, which is what a ``TICK_SIZE``-mode venue reports, so the reading
    under test is the unambiguous one: ``price_precision = 2``, ``quantity_precision = 8``,
    ``max_order_quantity = 1000``.
    """
    return {
        "symbol": SYMBOL,
        "base": "BTC",
        "quote": "USDT",
        "type": "spot",
        "active": True,
        "precision": {"price": 0.01, "amount": 0.00000001},
        "limits": {"amount": {"min": 0.0001, "max": 1000.0}},
    }


def _config(**overrides: Any) -> sim.SessionConfig:
    """The session's frozen configuration, built the way task 25.2 builds it at start."""
    metadata = sim.resolve_market_metadata(
        {SYMBOL: _market_entry()}, exchange_id=EXCHANGE, symbol=SYMBOL
    )
    config = sim.freeze_session_config(
        metadata=metadata,
        currency="USD",
        market_data_source="mds.watch_ohlcv",
        **{k: v for k, v in overrides.items() if k in ("fee_rate", "slippage_rate", "participation_rate")},
    )
    rest = {k: v for k, v in overrides.items() if k not in ("fee_rate", "slippage_rate", "participation_rate")}
    return dataclasses.replace(config, **rest) if rest else config


def _session_row(feed_state: str = "HEALTHY", user_id: str = USER) -> Dict[str, Any]:
    """A ``paper_sessions`` row in ``SESSION_FEED_SELECT``'s shape."""
    return {
        "id": SESSION,
        "user_id": user_id,
        "session_state": "RUNNING",
        "environment": "PAPER",
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": "1m",
        "market_data_source": "mds.watch_ohlcv",
        "feed_state": feed_state,
        "feed_transport": "WEBSOCKET",
        "event_sequence": 0,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def _seed(
    *,
    feed_state: str = "HEALTHY",
    capital: Decimal = CAPITAL,
    **double_kwargs: Any,
) -> Tuple[FakeSupabase, Dict[str, Any], str]:
    """A double carrying one running session and its isolated Paper_Account.

    The account is created through ``paper_repository.get_or_create_account``, so the premise these
    tests run under is the premise production runs under (Requirements 17.1, 17.2).
    """
    repo.reset_persistence_probe()
    supabase = FakeSupabase(sessions=[_session_row(feed_state)], **double_kwargs)
    account = repo.get_or_create_account(
        supabase, USER, "USD", SESSION, initial_capital=capital
    )
    # The premise's own statements are cleared, so ``_wrote_since`` and the tenancy assertions read
    # what the code under test issued rather than what the fixture did to set it up.
    supabase.statements.clear()
    supabase.ops.clear()
    return (supabase, _session_row(feed_state), str(account["id"]))


def _mark(supabase: FakeSupabase) -> int:
    """How many statements have been issued so far, for :func:`_wrote_since`."""
    return len(supabase.statements)


def _wrote_since(supabase: FakeSupabase, mark: int = 0) -> List[Tuple[str, str]]:
    """The ``(table, op)`` of every write issued after ``mark``.

    ``FakeSupabase.wrote_anything`` cannot serve here: the seeding writes the account row, so it is
    always ``True`` by the time a test starts. "Nothing was written" is a claim about the statements
    the code under test issued, and this is that list.
    """
    return [
        (q.table_name, q.op)
        for q in supabase.statements[mark:]
        if q.op in ("insert", "update")
    ]


def _event(
    *,
    close: str = "60000",
    low: Optional[str] = None,
    high: Optional[str] = None,
    volume: Optional[str] = None,
    ask: Optional[str] = None,
    bid: Optional[str] = None,
    source_event_id: str = "evt-1",
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One validated market event, as a mapping with **exact decimal strings**.

    Strings and not floats: ``paper_market_events.payload`` stores decimal strings for exactly this
    reason, and ``paper_simulator._event_decimal`` refuses a ``float`` (Requirement 18.1). A test
    that passed floats would be testing a path production cannot take.
    """
    payload: Dict[str, Any] = {
        "source_event_id": source_event_id,
        "symbol": SYMBOL,
        "timeframe": "1m",
        "event_timestamp": (at or NOW).isoformat(),
        "close": close,
    }
    for name, value in (("low", low), ("high", high), ("volume", volume), ("ask", ask), ("bid", bid)):
        if value is not None:
            payload[name] = value
    return payload


def _intent(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "symbol": SYMBOL,
        "side": "buy",
        "order_type": "market",
        "quantity": "0.5",
    }
    payload.update(overrides)
    return payload


def _submit(supabase: Any, session: Dict[str, Any], account_id: str, **kwargs: Any) -> Any:
    config = kwargs.pop("config", None) or _config()
    intent = kwargs.pop("intent", None) or _intent()
    return _run_coroutine(
        sim.submit_intent(
            supabase,
            session,
            intent,
            config=config,
            account_id=account_id,
            **kwargs,
        )
    )


def _snapshot(supabase: FakeSupabase) -> Dict[str, Any]:
    """Everything "nothing changed" is a claim about, as plain comparable structures."""
    return {
        "accounts": [dict(r) for r in supabase.accounts],
        "orders": [dict(r) for r in supabase.orders],
        "fills": [dict(r) for r in supabase.fills],
        "positions": [dict(r) for r in supabase.positions],
        "balance_events": [dict(r) for r in supabase.balance_events],
        "trades": [dict(r) for r in supabase.trades],
        "equity_snapshots": [dict(r) for r in supabase.equity_snapshots],
    }


# ══════════════════════════════════════════════════════════════════════════
#  25.3 - THE IDEMPOTENCY PROBE (Requirements 16.8, 16.11, 16.15)
# ══════════════════════════════════════════════════════════════════════════


def test_a_repeated_intent_returns_the_recorded_order_and_moves_no_money() -> None:
    """Requirement 16.8: the same key with identical parameters yields exactly one order."""
    supabase, session, account_id = _seed()
    config = _config()
    intent = _intent(order_type="limit", limit_price="60000", idempotency_key="key-1")

    first = _submit(supabase, session, account_id, config=config, intent=intent)
    after_first = _snapshot(supabase)

    second = _submit(supabase, session, account_id, config=config, intent=intent)

    assert second.duplicate is True
    assert second.order["id"] == first.order["id"]
    assert len(supabase.orders) == 1
    # No second order AND no balance movement - two separate claims in Requirement 16.8.
    assert _snapshot(supabase) == after_first


def test_a_reused_key_with_different_parameters_is_a_409_and_changes_nothing() -> None:
    """Requirement 16.15: no order is created and the recorded one is left unchanged."""
    supabase, session, account_id = _seed()
    config = _config()
    first = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(order_type="limit", limit_price="60000", idempotency_key="key-1"),
    )
    before = _snapshot(supabase)

    with pytest.raises(sim.PaperIdempotencyConflict) as caught:
        _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=_intent(
                order_type="limit",
                limit_price="60000",
                quantity="0.75",
                idempotency_key="key-1",
            ),
        )

    error = caught.value
    assert error.code == PAPER_IDEMPOTENCY_CONFLICT
    assert error.http_status == 409
    assert error.details["order_id"] == first.order["id"]
    assert error.details["fingerprint"] != error.details["recorded_fingerprint"]
    assert _snapshot(supabase) == before


def test_the_fingerprint_ignores_trailing_zeros_but_not_a_different_number() -> None:
    """A padded quantity is the same order; a different quantity is not.

    Without the canonicalisation a client that sent ``"0.5"`` and retried with ``"0.50"`` would get
    a 409 for repeating the same order, which is the failure idempotency exists to prevent.
    """
    padded = sim.order_fingerprint(_intent(quantity="0.50"))
    assert sim.order_fingerprint(_intent(quantity="0.5")) == padded
    assert sim.order_fingerprint(_intent(quantity="0.500000")) == padded
    assert sim.order_fingerprint(_intent(quantity="0.51")) != padded
    assert sim.order_fingerprint(_intent(side="sell")) != padded


def test_an_over_long_idempotency_key_is_refused_before_the_database() -> None:
    """Task 25.3: ``chk_paper_order_idem_len`` is a backstop, not the error surface."""
    supabase, session, account_id = _seed()
    with pytest.raises(sim.PaperOrderInvalid) as caught:
        _submit(
            supabase,
            session,
            account_id,
            intent=_intent(idempotency_key="k" * (sim.IDEMPOTENCY_KEY_MAX_CHARS + 1)),
        )
    assert caught.value.details["validation"] == "IDEMPOTENCY_KEY_LENGTH"
    assert caught.value.http_status == 400
    assert _wrote_since(supabase) == []


# ══════════════════════════════════════════════════════════════════════════
#  25.3 - THE EIGHT REJECTION REASONS (Requirement 16.5)
# ══════════════════════════════════════════════════════════════════════════

#: The four whose intent the ``paper_orders`` CHECK constraints permit, so Requirement 16.5's
#: persisted ``REJECTED`` order exists.
PERSISTED_REJECTIONS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    (sim.REJECTION_QUANTITY_ABOVE_MAX, {"quantity": "2000"}),
    (sim.REJECTION_QUANTITY_PRECISION, {"quantity": "0.000000001"}),
    (sim.REJECTION_SYMBOL_NOT_VALIDATED, {"symbol": "ETH/USDT"}),
    (
        sim.REJECTION_LIMIT_PRICE_PRECISION,
        {"order_type": "limit", "limit_price": "60000.001"},
    ),
)

#: The four whose intent those constraints refuse, so no row can exist. See
#: ``paper_simulator.ORDER_COLUMN_CONSTRAINTS``.
UNREPRESENTABLE_REJECTIONS: Tuple[Tuple[str, Dict[str, Any], str], ...] = (
    (sim.REJECTION_QUANTITY_NOT_POSITIVE, {"quantity": "0"}, "chk_paper_order_quantity"),
    (sim.REJECTION_QUANTITY_NOT_POSITIVE, {"quantity": "-1"}, "chk_paper_order_quantity"),
    (
        sim.REJECTION_LIMIT_PRICE_NOT_POSITIVE,
        {"order_type": "limit", "limit_price": "0"},
        "chk_paper_order_limit_price",
    ),
    (
        sim.REJECTION_ORDER_TYPE_UNSUPPORTED,
        {"order_type": "stop"},
        "chk_paper_order_type",
    ),
    (sim.REJECTION_SIDE_UNSUPPORTED, {"side": "short"}, "chk_paper_order_side"),
)


@pytest.mark.parametrize(
    "reason,overrides", PERSISTED_REJECTIONS, ids=[r for r, _ in PERSISTED_REJECTIONS]
)
def test_a_failed_static_check_persists_a_rejected_order_from_created(
    reason: str, overrides: Dict[str, Any]
) -> None:
    """Requirement 16.5: ``REJECTED`` from ``CREATED``, naming the check, balances untouched."""
    supabase, session, account_id = _seed()
    before_account = dict(supabase.accounts[0])

    outcome = _submit(supabase, session, account_id, intent=_intent(**overrides))

    assert outcome.rejected is True
    assert outcome.rejection_reason == reason
    assert outcome.order["order_state"] == PaperOrderState.REJECTED.value
    assert outcome.order["rejection_reason"] == reason
    assert outcome.order["legacy_status"] == "REJECTED"

    # From CREATED: the INSERT lands at CREATED and the UPDATE is guarded by it.
    inserts = supabase.statements_on(repo.ORDERS_TABLE, "insert")
    assert [q.payload["order_state"] for q in inserts] == [PaperOrderState.CREATED.value]
    updates = supabase.statements_on(repo.ORDERS_TABLE, "update")
    assert updates[-1].filter_value("order_state") == PaperOrderState.CREATED.value

    # Balances and positions untouched, and no statement was even issued against them.
    assert dict(supabase.accounts[0]) == before_account
    assert supabase.positions == []
    assert supabase.balance_events == []
    assert supabase.statements_on(repo.ACCOUNTS_TABLE, "update") == []


@pytest.mark.parametrize(
    "reason,overrides,constraint",
    UNREPRESENTABLE_REJECTIONS,
    ids=[f"{r}-{c}" for r, _, c in UNREPRESENTABLE_REJECTIONS],
)
def test_a_rejection_the_column_constraints_forbid_is_a_400_naming_the_same_reason(
    reason: str, overrides: Dict[str, Any], constraint: str
) -> None:
    """The recorded Requirement 16.5 gap: same reason, no row, and the constraint named.

    ``009_paper_trading.sql`` declares ``quantity > 0``, ``limit_price IS NULL OR limit_price > 0``
    and the two vocabulary CHECKs, so an order carrying these values cannot exist. Relaxing them to
    let a rejected order hold a nonsense value would weaken a control that currently keeps one out
    of the table entirely, so they stand and the divergence is reported.
    """
    supabase, session, account_id = _seed()

    with pytest.raises(sim.PaperOrderInvalid) as caught:
        _submit(supabase, session, account_id, intent=_intent(**overrides))

    error = caught.value
    assert error.code == PAPER_ORDER_INVALID
    assert error.http_status == 400
    assert error.details["validation"] == reason
    assert error.details["blocked_by"] == constraint
    assert error.details["persisted"] is False
    assert supabase.orders == []
    assert _wrote_since(supabase) == []


@pytest.mark.parametrize(
    "reason,narrowing,overrides",
    (
        (
            sim.REJECTION_ORDER_TYPE_UNSUPPORTED,
            {"supported_order_types": ("market",)},
            {"order_type": "limit", "limit_price": "60000"},
        ),
        (
            sim.REJECTION_SIDE_UNSUPPORTED,
            {"supported_sides": ("buy",)},
            {"side": "sell"},
        ),
    ),
    ids=["ORDER_TYPE_UNSUPPORTED", "SIDE_UNSUPPORTED"],
)
def test_a_narrower_recorded_vocabulary_does_persist_the_rejection(
    reason: str, narrowing: Dict[str, Any], overrides: Dict[str, Any]
) -> None:
    """The other branch of the same two reasons, where Requirement 16.5 IS satisfied in full.

    A session whose recorded ``supported_order_types`` or ``supported_sides`` is narrower than the
    column's CHECK rejects a value the column can still store, so the order is persisted
    ``REJECTED`` naming the check - exactly as the requirement asks.
    """
    supabase, session, account_id = _seed()
    outcome = _submit(
        supabase,
        session,
        account_id,
        config=_config(**narrowing),
        intent=_intent(**overrides),
    )
    assert outcome.rejection_reason == reason
    assert outcome.order["order_state"] == PaperOrderState.REJECTED.value
    assert outcome.order["rejection_reason"] == reason
    assert supabase.balance_events == []


def test_the_first_failing_check_is_the_reported_one() -> None:
    """The order of Requirement 16.5's checks is contractual, not incidental.

    An intent that is both above the maximum and above the precision reports
    ``QUANTITY_ABOVE_MAX``, because that is the check ``design.md``'s ``first_of(...)`` evaluates
    first. Without a fixed order the reason a caller reads would depend on how the conditions
    happen to be arranged in the source.
    """
    assert (
        sim.static_rejection_reason(_intent(quantity="2000.000000001"), _config())
        == sim.REJECTION_QUANTITY_ABOVE_MAX
    )
    assert sim.REJECTION_REASONS == (
        "QUANTITY_NOT_POSITIVE",
        "QUANTITY_ABOVE_MAX",
        "QUANTITY_PRECISION",
        "SYMBOL_NOT_VALIDATED",
        "ORDER_TYPE_UNSUPPORTED",
        "SIDE_UNSUPPORTED",
        "LIMIT_PRICE_NOT_POSITIVE",
        "LIMIT_PRICE_PRECISION",
        "NO_VALIDATED_PRICE",
        "INSUFFICIENT_FUNDS",
    )


def test_a_padded_quantity_is_not_a_precision_rejection() -> None:
    """``decimal_places`` normalizes first, so a client that padded has not exceeded anything."""
    assert sim.decimal_places(Decimal("1.500")) == 1
    assert sim.decimal_places(Decimal("100")) == 0
    assert sim.decimal_places(Decimal("0.00000001")) == 8
    assert sim.static_rejection_reason(_intent(quantity="0.50000000000"), _config()) is None


# ══════════════════════════════════════════════════════════════════════════
#  25.3 - THE PRICE AND THE FUNDS (Requirements 14.9, 16.6)
# ══════════════════════════════════════════════════════════════════════════


def test_a_market_order_with_no_validated_price_is_rejected_and_nothing_is_synthesised() -> None:
    """Requirement 14.9: no validated price means ``NO_VALIDATED_PRICE``, never a made-up one."""
    supabase, session, account_id = _seed()

    outcome = _submit(supabase, session, account_id, latest_event=None)

    assert outcome.rejection_reason == sim.REJECTION_NO_VALIDATED_PRICE
    assert outcome.order["order_state"] == PaperOrderState.REJECTED.value
    # The order records no reference price, because there was none to record.
    assert outcome.order["reference_price"] is None
    assert supabase.fills == []
    assert supabase.balance_events == []


def test_an_event_that_states_no_usable_price_is_still_no_validated_price() -> None:
    """``reference_price`` answers ``None`` rather than falling back to something."""
    assert sim.reference_price(None, "buy") is None
    assert sim.reference_price(_event(close="0"), "buy") is None
    # The ask is preferred for a buy and the bid for a sell, when the source supplies them.
    quoted = _event(close="60000", ask="60010", bid="59990")
    assert sim.reference_price(quoted, "buy") == Decimal("60010")
    assert sim.reference_price(quoted, "sell") == Decimal("59990")
    # And the close is the answer when it does not.
    assert sim.reference_price(_event(close="60000"), "buy") == Decimal("60000")


def test_insufficient_funds_rejects_and_locks_nothing() -> None:
    """Requirement 16.6: rejected on the balance read in the attempt, with nothing locked."""
    supabase, session, account_id = _seed(capital=Decimal("100"))
    before = dict(supabase.accounts[0])

    outcome = _submit(
        supabase,
        session,
        account_id,
        intent=_intent(order_type="limit", quantity="1", limit_price="60000"),
    )

    assert outcome.rejection_reason == sim.REJECTION_INSUFFICIENT_FUNDS
    assert outcome.order["order_state"] == PaperOrderState.REJECTED.value
    assert outcome.required_funds is not None
    assert outcome.required_funds > Decimal("100")
    # Locks nothing: the balances are byte-for-byte what they were, and no ORDER_LOCK was written.
    assert dict(supabase.accounts[0]) == before
    assert [r["cause"] for r in supabase.balance_events] == []
    assert supabase.statements_on(repo.ACCOUNTS_TABLE, "update") == []


def test_required_funds_carry_the_fee_and_slippage_allowances() -> None:
    """Requirement 16.6: "plus the fees and slippage of the session's recorded configuration"."""
    config = _config()
    required = sim.required_funds(
        {"quantity": Decimal("1")}, Decimal("60000"), config.accounting()
    )
    # 60000 * (1 + 0.0010 + 0.0005) = 60090.00, exactly, at the USD minor-unit scale.
    assert required == Decimal("60090.00")


# ══════════════════════════════════════════════════════════════════════════
#  25.3 - THE ACCEPT PATH (Requirement 16.6, 18.4, 18.11)
# ══════════════════════════════════════════════════════════════════════════


def test_a_limit_order_is_accepted_from_created_and_locks_its_required_funds() -> None:
    """Task 25.3 step 5: ``CREATED -> ACCEPTED``, then ``available -> locked``."""
    supabase, session, account_id = _seed()

    outcome = _submit(
        supabase,
        session,
        account_id,
        intent=_intent(order_type="limit", quantity="1", limit_price="60000"),
    )

    assert outcome.accepted is True
    assert outcome.order["order_state"] == PaperOrderState.ACCEPTED.value
    assert outcome.order["legacy_status"] == "OPEN"
    assert outcome.locked == Decimal("60090.00")

    account = dict(supabase.accounts[0])
    assert Decimal(account["available_balance"]) == CAPITAL - Decimal("60090.00")
    assert Decimal(account["locked_balance"]) == Decimal("60090.00")
    # available + locked is unchanged, so Requirement 18.3's identity is untouched by a lock.
    assert Decimal(account["available_balance"]) + Decimal(account["locked_balance"]) == CAPITAL

    ledger = [r for r in supabase.balance_events if r["cause"] == "ORDER_LOCK"]
    assert len(ledger) == 1
    assert Decimal(ledger[0]["available_delta"]) == -Decimal("60090.00")
    assert Decimal(ledger[0]["locked_delta"]) == Decimal("60090.00")

    # A resting limit order is not filled by its acceptance.
    assert supabase.fills == []


def test_a_market_order_is_accepted_and_then_filled_after_the_accept_returns() -> None:
    """Task 25.3: the accepted order is durable **before** it is filled."""
    supabase, session, account_id = _seed()

    outcome = _submit(
        supabase,
        session,
        account_id,
        intent=_intent(quantity="1"),
        latest_event=_event(close="60000"),
    )

    assert outcome.fill is not None
    assert outcome.fill.applied is True
    assert outcome.order["order_state"] == PaperOrderState.FILLED.value

    # CREATED -> ACCEPTED -> FILLED, in that order, each guarded by the state it was read in.
    guarded = [
        q.filter_value("order_state")
        for q in supabase.statements_on(repo.ORDERS_TABLE, "update")
    ]
    assert guarded == [PaperOrderState.CREATED.value, PaperOrderState.ACCEPTED.value]

    # A market order locks nothing: the only ledger row is the fill's.
    assert [r["cause"] for r in supabase.balance_events] == ["FILL"]


# ══════════════════════════════════════════════════════════════════════════
#  25.5 - THE DETERMINISTIC FILL MODEL (Requirements 16.12, 16.13, 16.14)
# ══════════════════════════════════════════════════════════════════════════


def test_a_market_order_slips_adversely_and_only_adversely() -> None:
    """Task 25.5: ``reference × (1 ± slippage_rate)``, a buy up and a sell down."""
    config = _config(slippage_rate=Decimal("0.01"))
    assert sim.market_fill_price(Decimal("100"), "buy", config) == Decimal("101.00")
    assert sim.market_fill_price(Decimal("100"), "sell", config) == Decimal("99.00")


def test_a_market_fill_records_its_fee_and_its_slippage_in_minor_units() -> None:
    """Requirement 16.12: the fee and the slippage applied are recorded on the fill."""
    supabase, session, account_id = _seed()
    config = _config(slippage_rate=Decimal("0.01"), fee_rate=Decimal("0.001"))

    outcome = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(quantity="1"),
        latest_event=_event(close="1000"),
    )

    fill = supabase.fills[0]
    # reference 1000, buy, 1% adverse -> 1010.00. slippage cost = |1010 - 1000| * 1 = 10.00.
    assert Decimal(fill["price"]) == Decimal("1010.00")
    assert fill["slippage_minor"] == 1000
    # fee = 1 * 1010.00 * 0.001 = 1.01 -> 101 cents.
    assert fill["fee_minor"] == 101
    assert outcome.fill is not None
    assert outcome.fill.fee == Decimal("1.01")


def test_a_resting_limit_order_fills_at_exactly_its_limit_with_no_favourable_slippage() -> None:
    """Task 25.5: an improvement on a resting limit would be an invented price.

    The candle traded **through** the limit - a buy limit at 1000 against a candle whose low was
    900 - so a naive model would fill at 900 and report a better result than the market gave. The
    fill is at exactly 1000, and ``slippage_minor`` is zero.
    """
    supabase, session, account_id = _seed()
    config = _config(slippage_rate=Decimal("0.01"), participation_rate=Decimal("0"))

    accepted = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(order_type="limit", quantity="1", limit_price="1000"),
    )
    assert accepted.accepted is True

    outcomes = _run_coroutine(
        sim.check_resting_orders(
            supabase,
            session,
            _event(close="950", low="900", high="1100", source_event_id="evt-2"),
            config=config,
            account_id=account_id,
        )
    )

    assert len(outcomes) == 1
    assert outcomes[0].applied is True
    fill = supabase.fills[0]
    assert Decimal(fill["price"]) == Decimal("1000.00")
    assert fill["slippage_minor"] == 0
    assert outcomes[0].order["order_state"] == PaperOrderState.FILLED.value


def test_a_resting_sell_limit_fills_only_when_the_high_reaches_it() -> None:
    """The trigger condition, both sides, on the candle field and on the tick field."""
    buy = {"side": "buy", "limit_price": "1000"}
    sell = {"side": "sell", "limit_price": "1000"}
    assert sim.limit_fill_triggered(buy, _event(close="1100", low="999")) is True
    assert sim.limit_fill_triggered(buy, _event(close="900", low="1001")) is False
    assert sim.limit_fill_triggered(sell, _event(close="900", high="1001")) is True
    assert sim.limit_fill_triggered(sell, _event(close="1100", high="999")) is False
    # No candle fields: a tick source's ``last``, then ``close``.
    assert sim.limit_fill_triggered(buy, {"last": "1000"}) is True
    assert sim.limit_fill_triggered(buy, {"close": "1000"}) is True
    # An order with no limit price never triggers, and neither does an unknown side.
    assert sim.limit_fill_triggered({"side": "buy"}, _event(low="1")) is False
    assert sim.limit_fill_triggered({"side": "short", "limit_price": "1"}, _event(low="1")) is False


def test_the_partial_fill_is_a_participation_cap_and_not_a_probability() -> None:
    """Task 25.5: ``MIN(remaining, quantize(volume × participation_rate))``, deterministically."""
    config = _config(participation_rate=Decimal("0.10"))
    order = {"quantity": "1", "filled_quantity": "0"}

    # A volume of 1 at 10% caps the fill at 0.1, whatever remains.
    assert sim.fillable_quantity(order, _event(volume="1"), config) == Decimal("0.10000000")
    # A volume large enough not to bind leaves the whole remainder.
    assert sim.fillable_quantity(order, _event(volume="1000"), config) == Decimal("1.00000000")
    # No volume reported -> the whole remaining quantity, not a guessed cap.
    assert sim.fillable_quantity(order, _event(), config) == Decimal("1.00000000")
    # The remainder is what is left, not what was ordered.
    assert sim.fillable_quantity(
        {"quantity": "1", "filled_quantity": "0.95"}, _event(volume="1"), config
    ) == Decimal("0.05000000")
    # Deterministic: the same inputs, ten times, the same answer.
    assert {
        sim.fillable_quantity(order, _event(volume="1"), config) for _ in range(10)
    } == {Decimal("0.10000000")}


def test_a_capped_fill_leaves_the_order_partially_filled_and_the_next_event_finishes_it() -> None:
    """Requirements 16.13, 16.14: ``PARTIALLY_FILLED`` below the quantity, ``FILLED`` at it."""
    supabase, session, account_id = _seed()
    config = _config(participation_rate=Decimal("0.5"), slippage_rate=Decimal("0"), fee_rate=Decimal("0"))

    _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(order_type="limit", quantity="1", limit_price="1000"),
    )

    first = _run_coroutine(
        sim.check_resting_orders(
            supabase,
            session,
            _event(close="1000", low="900", volume="1", source_event_id="evt-a"),
            config=config,
            account_id=account_id,
        )
    )
    assert first[0].order["order_state"] == PaperOrderState.PARTIALLY_FILLED.value
    assert Decimal(first[0].order["filled_quantity"]) == Decimal("0.5")

    second = _run_coroutine(
        sim.check_resting_orders(
            supabase,
            session,
            _event(
                close="1000",
                low="900",
                volume="1",
                source_event_id="evt-b",
                at=NOW + timedelta(minutes=1),
            ),
            config=config,
            account_id=account_id,
        )
    )
    assert second[0].order["order_state"] == PaperOrderState.FILLED.value
    assert Decimal(second[0].order["filled_quantity"]) == Decimal("1")
    # Two fills, two equity points, and the order's filled quantity equals their sum.
    assert len(supabase.fills) == 2
    assert [r["cause"] for r in supabase.equity_snapshots] == ["FILL", "FILL"]
    assert sum(Decimal(r["quantity"]) for r in supabase.fills) == Decimal("1")


def test_replaying_the_same_event_fills_the_resting_order_only_once() -> None:
    """The ``fill_event_id`` is derived from the order and the event, so a replay is a no-op."""
    supabase, session, account_id = _seed()
    config = _config(participation_rate=Decimal("0.5"), slippage_rate=Decimal("0"))
    _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(order_type="limit", quantity="1", limit_price="1000"),
    )
    event = _event(close="1000", low="900", volume="1", source_event_id="evt-a")

    _run_coroutine(
        sim.check_resting_orders(
            supabase, session, event, config=config, account_id=account_id
        )
    )
    after_first = _snapshot(supabase)
    replayed = _run_coroutine(
        sim.check_resting_orders(
            supabase, session, event, config=config, account_id=account_id
        )
    )

    assert [o.outcome for o in replayed] == [sim.FILL_DUPLICATE]
    assert _snapshot(supabase) == after_first


def test_check_resting_orders_writes_no_fill_of_its_own() -> None:
    """Task 25.5: it is the one caller for limit orders and routes through ``apply_fill``.

    Asserted on the statements: the only read this function issues itself is the one that finds the
    resting orders, and every write against ``paper_fills`` comes from ``apply_fill``. A second
    write path would mean the four guards, the feed gate and the invariant assert could be bypassed.
    """
    import inspect

    source = inspect.getsource(sim.check_resting_orders)
    assert "insert_fill" not in source
    assert "apply_fill(" in source
    assert "bump_version" not in source


# ══════════════════════════════════════════════════════════════════════════
#  25.4 - THE FOUR GUARDS (Requirements 16.3, 16.7, 16.9, 18.14)
# ══════════════════════════════════════════════════════════════════════════


def _accepted_market_order(
    supabase: FakeSupabase,
    account_id: str,
    *,
    quantity: str = "1",
    state: Any = None,
    side: str = "buy",
    fingerprint: str = "fp-test",
) -> Dict[str, Any]:
    """One ``paper_orders`` row at ``ACCEPTED`` (or ``state``), written through the repository.

    ``side`` and ``fingerprint`` are keyword-only with the values every test in this file already
    used, so nothing here changes. They exist because ``tests/property/test_paper_idempotence.py``
    (task 25.11, P-21) has to build a **sell** order to close a position and needs more than one
    order in one store: a second copy of these two repository calls in that file would be a second
    account of what "an accepted order" is.
    """
    created = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side=side,
        order_type="market",
        quantity=quantity,
        reference_price="1000",
        fingerprint=fingerprint,
        order_state=PaperOrderState.CREATED,
    )
    row = repo.update_order(
        supabase,
        user_id=USER,
        order_id=created["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )
    if state is not None and state != PaperOrderState.ACCEPTED:
        row = repo.update_order(
            supabase,
            user_id=USER,
            order_id=created["id"],
            order_state=state,
            expected_state=PaperOrderState.ACCEPTED,
        )
    return row


def _fill(
    supabase: Any,
    session: Dict[str, Any],
    order: Dict[str, Any],
    *,
    config: Optional[sim.SessionConfig] = None,
    quantity: str = "1",
    price: str = "1000",
    fill_event_id: str = "fill-1",
    **kwargs: Any,
) -> sim.FillOutcome:
    return _run_coroutine(
        sim.apply_fill(
            supabase,
            session,
            order,
            config=config or _config(slippage_rate=Decimal("0"), fee_rate=Decimal("0")),
            quantity=quantity,
            price=price,
            fill_event_id=fill_event_id,
            filled_at=NOW,
            **kwargs,
        )
    )


@pytest.mark.parametrize(
    "state",
    (PaperOrderState.FILLED, PaperOrderState.CANCELLED),
    ids=lambda s: s.value,
)
def test_a_fill_against_a_terminal_order_changes_nothing(state: Any) -> None:
    """Requirement 16.3: no transition leaves a terminal state, and nothing else moves either."""
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, state=state)
    before = _snapshot(supabase)

    outcome = _fill(supabase, session, order)

    assert outcome.outcome == sim.FILL_TERMINAL
    assert outcome.applied is False
    assert outcome.admission is None
    assert _snapshot(supabase) == before


def test_a_repeated_fill_event_id_changes_nothing_at_all() -> None:
    """Requirements 16.9, 18.13: the same event twice is the same result as once.

    Asserted across every table, including ``paper_equity_snapshots`` - Requirement 18.13's "SHALL
    persist no additional equity snapshot for a repeated event identifier" is a separate claim from
    the balance one.
    """
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="2")

    first = _fill(supabase, session, order, quantity="1", fill_event_id="fill-1")
    assert first.applied is True
    after_first = _snapshot(supabase)

    second = _fill(supabase, session, order, quantity="1", fill_event_id="fill-1")

    assert second.outcome == sim.FILL_DUPLICATE
    assert second.applied is False
    assert _snapshot(supabase) == after_first
    assert len(supabase.fills) == 1
    assert len(supabase.equity_snapshots) == 1


def test_an_over_fill_is_refused_and_leaves_everything_unchanged() -> None:
    """Requirement 16.7: refused, with the state, position, balance and realized PnL untouched."""
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    first = _fill(supabase, session, order, quantity="0.6", fill_event_id="fill-1")
    assert first.order["order_state"] == PaperOrderState.PARTIALLY_FILLED.value
    before = _snapshot(supabase)

    with pytest.raises(sim.PaperOverFill) as caught:
        _fill(supabase, session, first.order, quantity="0.5", fill_event_id="fill-2")

    error = caught.value
    assert error.code == PAPER_OVER_FILL
    assert error.http_status == 409
    assert error.details["ordered"] == "1.00000000"
    assert error.details["filled"] == "0.60000000"
    assert error.details["requested"] == "0.50000000"
    assert _snapshot(supabase) == before


def test_the_over_fill_guard_reads_the_fill_rows_and_not_the_order_column() -> None:
    """P-20's oracle is the sum over ``paper_fills``, so a drifted column must not hide an over-fill.

    The order's ``filled_quantity`` column is set back to zero behind the simulator's back while the
    fill rows still say 0.6. A guard that trusted the column would admit a further 0.5; the guard
    recomputes from the rows and refuses it.
    """
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    _fill(supabase, session, order, quantity="0.6", fill_event_id="fill-1")

    for row in supabase.orders:
        row["filled_quantity"] = "0"

    with pytest.raises(sim.PaperOverFill):
        _fill(supabase, session, supabase.orders[0], quantity="0.5", fill_event_id="fill-2")


@pytest.mark.parametrize(
    "quantity,target",
    (("1", "FILLED"), ("0.4", "PARTIALLY_FILLED")),
    ids=("FILLED", "PARTIALLY_FILLED"),
)
def test_an_illegal_transition_is_refused_before_a_single_statement_is_issued(
    quantity: str, target: str
) -> None:
    """Requirements 16.2, 16.4, 16.10 - the regression evidence for a defect P-19 found.

    ``apply_fill`` used to decide its target at **write 5** and let
    ``paper_repository.update_order`` refuse an illegal pair there. By that point the fill row, the
    account balance, the ledger row and the position had all been written, and this transport has no
    ``ROLLBACK``: a fill against an order still at ``CREATED`` moved 1000 out of the available
    balance, wrote a ``paper_fills`` row, opened a position, appended a ``FILL`` ledger row - and
    then raised ``ValueError: CREATED -> FILLED is not a permitted paper order transition
    (Requirement 16.2); nothing was written``. The stored state was indeed unchanged, which is all
    Requirement 16.4 asks; the partial write is what Requirement 16.10 forbids.

    The transition is now checked next to the over-fill guard, before the first statement, and the
    refusal is a 400 naming the rejected pair. Both fill targets are asserted, because the target is
    what the guard computes and a check that only covered one of them would leave the other late.

    No production caller reaches ``apply_fill`` with a ``CREATED`` order - a market order's fill runs
    after its accept and ``check_resting_orders`` reads ``legacy_status='OPEN'`` - but this is the
    module's single public write path for a fill, and a public write path does not get to assume its
    callers.
    """
    supabase, session, account_id = _seed()
    created = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity="1",
        reference_price="1000",
        fingerprint="fp-illegal",
        order_state=PaperOrderState.CREATED,
    )
    before = _snapshot(supabase)
    mark = _mark(supabase)

    with pytest.raises(sim.PaperOrderInvalid) as caught:
        _fill(supabase, session, created, quantity=quantity, price="1000")

    error = caught.value
    assert error.code == PAPER_ORDER_INVALID
    assert error.http_status == 400
    assert error.details["validation"] == "ILLEGAL_TRANSITION"
    assert error.details["from"] == PaperOrderState.CREATED.value
    assert error.details["to"] == target
    assert error.details["persisted"] is False

    # Not one statement was issued against any write table, so "unchanged" is a fact about the
    # whole account rather than only about the order's state column.
    assert _wrote_since(supabase, mark) == []
    assert _snapshot(supabase) == before
    assert supabase.orders[0]["order_state"] == PaperOrderState.CREATED.value
    assert Decimal(supabase.accounts[0]["available_balance"]) == CAPITAL


def test_an_invariant_breach_rolls_everything_back_and_names_the_invariant() -> None:
    """Requirement 18.14: nothing is written, and ``details["invariant"]`` says which check failed.

    The account holds 100 and the fill costs 1000, so ``paper_accounting.apply_fill`` refuses it
    for ``available_balance`` (Requirement 18.4). Over PostgREST there is no ``ROLLBACK``, so the
    assert runs on the computed state **before the first statement** - which is why "unchanged"
    here is a fact about what was never issued rather than about what was undone.
    """
    supabase, session, account_id = _seed(capital=Decimal("100"))
    order = _accepted_market_order(supabase, account_id, quantity="1")
    before = _snapshot(supabase)
    mark = _mark(supabase)

    with pytest.raises(sim.PaperInvariantViolation) as caught:
        _fill(supabase, session, order, quantity="1", price="1000")

    error = caught.value
    assert error.code == PAPER_INVARIANT_VIOLATION
    assert error.http_status == 500
    assert error.details["invariant"] == "available_balance"
    assert error.details["phase"] == "apply_fill"
    assert _snapshot(supabase) == before
    # Not one statement was issued against any of the seven write tables.
    assert _wrote_since(supabase, mark) == []


def test_an_applied_fill_writes_every_row_the_task_names() -> None:
    """Task 25.4's write list, asserted as a list.

    ``paper_fills`` with its two minor-unit columns, the account at ``version + 1``, the position
    upsert, the balance event, the state transition, and the ``cause='FILL'`` equity snapshot. The
    ``paper_trades`` row is the one conditional member - it appears only when a position reaches
    size zero - and it is asserted in :func:`test_a_position_reaching_zero_writes_a_closed_trade`.
    """
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    version_before = supabase.accounts[0]["version"]

    outcome = _fill(supabase, session, order, quantity="1", price="1000")

    assert outcome.applied is True
    assert outcome.admission is not None and outcome.admission.feed_state == "HEALTHY"

    assert len(supabase.fills) == 1
    assert supabase.fills[0]["fee_minor"] == 0
    assert supabase.fills[0]["slippage_minor"] == 0
    assert supabase.accounts[0]["version"] == version_before + 1
    assert len(supabase.positions) == 1
    assert supabase.positions[0]["side"] == "LONG"
    assert Decimal(supabase.positions[0]["size"]) == Decimal("1")
    assert [r["cause"] for r in supabase.balance_events] == ["FILL"]
    assert outcome.order["order_state"] == PaperOrderState.FILLED.value
    assert [r["cause"] for r in supabase.equity_snapshots] == ["FILL"]
    assert supabase.trades == []

    # Requirement 18.6: a zero-fee, zero-slippage fill changes total_equity by exactly zero.
    assert Decimal(supabase.accounts[0]["total_equity"]) == CAPITAL


def test_a_fee_reduces_total_equity_by_exactly_the_recorded_fee() -> None:
    """Requirement 18.7, on the persisted figures rather than on the in-memory result."""
    supabase, session, account_id = _seed()
    config = _config(fee_rate=Decimal("0.001"), slippage_rate=Decimal("0"))
    order = _accepted_market_order(supabase, account_id, quantity="1")

    _fill(supabase, session, order, config=config, quantity="1", price="1000")

    fee_minor = supabase.fills[0]["fee_minor"]
    assert fee_minor == 100  # 1 * 1000 * 0.001 = 1.00
    assert Decimal(supabase.accounts[0]["total_equity"]) == CAPITAL - Decimal("1.00")


def test_a_position_reaching_zero_writes_a_closed_trade() -> None:
    """Requirement 18.10: a trade is closed when its position quantity reaches zero."""
    supabase, session, account_id = _seed()
    config = _config(fee_rate=Decimal("0"), slippage_rate=Decimal("0"))
    buy = _accepted_market_order(supabase, account_id, quantity="1")
    _fill(supabase, session, buy, config=config, quantity="1", price="1000", fill_event_id="f-1")

    sell = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side="sell",
        order_type="market",
        quantity="1",
        reference_price="1100",
        fingerprint="fp-sell",
        order_state=PaperOrderState.CREATED,
    )
    sell = repo.update_order(
        supabase,
        user_id=USER,
        order_id=sell["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )
    _fill(supabase, session, sell, config=config, quantity="1", price="1100", fill_event_id="f-2")

    assert len(supabase.trades) == 1
    trade = supabase.trades[0]
    assert trade["side"] == "LONG"
    assert Decimal(trade["realized_pnl"]) == Decimal("100.00")
    # The closed position persists at size zero rather than being deleted (Requirement 18.5).
    assert Decimal(supabase.positions[0]["size"]) == Decimal("0")
    assert supabase.positions[0]["closed_at"] is not None


# ══════════════════════════════════════════════════════════════════════════
#  25.4 - THE FEED GATE (Requirement 14.5's simulator half)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "feed_state", ("PENDING", "DEGRADED", "FALLBACK_REST", "TRANSPORT_UNKNOWN", "")
)
def test_no_fill_is_applied_while_the_feed_state_is_not_healthy(feed_state: str) -> None:
    """``TRADEABLE_FEED_STATES`` is exactly ``("HEALTHY",)`` - the whole of the gate's rule.

    ``FALLBACK_REST`` is in this list deliberately: Requirement 14.6 treats the REST fallback as a
    working feed, and ``paper_market_feed`` still refuses to *execute* on it. This test pins that
    the simulator honours the gate's answer rather than second-guessing it.
    """
    supabase, session, account_id = _seed(feed_state=feed_state)
    order = _accepted_market_order(supabase, account_id, quantity="1")
    before = _snapshot(supabase)

    with pytest.raises(FeedNotHealthy):
        _fill(supabase, session, order, quantity="1", price="1000")

    assert _snapshot(supabase) == before
    assert supabase.fills == []


def test_a_market_order_is_not_even_accepted_while_the_feed_is_degraded() -> None:
    """A market order fills on acceptance, so accepting one on a degraded feed creates a dud."""
    supabase, session, account_id = _seed(feed_state="DEGRADED")

    with pytest.raises(FeedNotHealthy):
        _submit(
            supabase,
            session,
            account_id,
            intent=_intent(quantity="1"),
            latest_event=_event(close="1000"),
        )

    assert supabase.orders == []
    assert _wrote_since(supabase) == []


def test_a_limit_order_is_accepted_on_a_degraded_feed_and_simply_does_not_fill() -> None:
    """The gate's own docstring: "refuse the market order and leave the resting limit order unfilled"."""
    supabase, session, account_id = _seed(feed_state="DEGRADED")
    config = _config()

    accepted = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(order_type="limit", quantity="1", limit_price="1000"),
    )
    assert accepted.order["order_state"] == PaperOrderState.ACCEPTED.value

    with pytest.raises(FeedNotHealthy):
        _run_coroutine(
            sim.check_resting_orders(
                supabase,
                session,
                _event(close="900", low="900", source_event_id="evt-2"),
                config=config,
                account_id=account_id,
            )
        )
    assert supabase.fills == []


def test_the_gate_is_consulted_on_the_row_read_inside_the_attempt() -> None:
    """Not on the row the caller passed in, which may be stale.

    The session row handed to :func:`apply_fill` says ``HEALTHY`` while the stored row says
    ``DEGRADED``. A gate that trusted the argument would fill; the gate re-reads and refuses. That
    is the difference between checking a value and checking the state.
    """
    supabase, session, account_id = _seed(feed_state="DEGRADED")
    order = _accepted_market_order(supabase, account_id, quantity="1")
    stale_but_healthy = _session_row("HEALTHY")

    with pytest.raises(FeedNotHealthy):
        _fill(supabase, stale_but_healthy, order, quantity="1", price="1000")
    assert supabase.fills == []


def test_a_terminal_or_duplicate_fill_is_a_no_op_even_on_a_degraded_feed() -> None:
    """The gate sits after the two no-op guards, and that ordering is deliberate.

    Requirements 16.3 and 16.9 say a terminal order and a repeated event change nothing *whatever
    else is true*. A gate placed ahead of them would turn a guaranteed no-op into a refusal, which
    is a different answer to the same question.
    """
    supabase, session, account_id = _seed(feed_state="HEALTHY")
    order = _accepted_market_order(supabase, account_id, quantity="1")
    applied = _fill(supabase, session, order, quantity="1", fill_event_id="fill-1")
    assert applied.applied is True

    for row in supabase.sessions:
        row["feed_state"] = "DEGRADED"
    before = _snapshot(supabase)

    # The order is now FILLED, so this is the terminal guard.
    assert _fill(supabase, session, applied.order, fill_event_id="fill-2").outcome == (
        sim.FILL_TERMINAL
    )
    assert _snapshot(supabase) == before


# ══════════════════════════════════════════════════════════════════════════
#  25.6 - THE RETRY AND CONFLICT PATH (Requirement 16.10)
# ══════════════════════════════════════════════════════════════════════════


def _version_racer(client: FakeSupabase, query: Any) -> None:
    """A concurrent writer that moves the account row after every read of it.

    Injected through the double's ``after_select`` seam, which fires once the read has completed and
    its rows are copied out - so the caller holds a correct image of a row that has since moved,
    which is exactly the race the version predicate exists to detect.
    """
    if query.table_name != repo.ACCOUNTS_TABLE or query.op != "select":
        return
    for row in client.accounts:
        row["version"] = int(row.get("version") or 1) + 1


def test_the_backoff_is_bounded_and_jitter_free() -> None:
    """Requirement 15.4: a replayed session must wait the same sequence, so no jitter."""
    assert sim.RETRY_ATTEMPTS == 3
    assert sim.RETRY_BACKOFF_SECONDS == (Decimal("0.05"), Decimal("0.10"))
    assert sim.retry_backoff_seconds(1) == Decimal("0.05")
    assert sim.retry_backoff_seconds(2) == Decimal("0.10")
    # Capped, not unbounded.
    assert sim.retry_backoff_seconds(9) == Decimal("0.10")
    with pytest.raises(ValueError):
        sim.retry_backoff_seconds(0)
    # Two runs of the same sequence are identical, which a jittered backoff could not be.
    assert [sim.retry_backoff_seconds(n) for n in (1, 2, 3)] == [
        sim.retry_backoff_seconds(n) for n in (1, 2, 3)
    ]


def _blocking_sleep_calls(module: Any) -> List[str]:
    """Every ``time.sleep`` CALL in ``module``, as ``"<line>: <expression>"`` strings.

    Asserted over the parse tree rather than over the text for a reason. The claim this test
    exists to defend is "the retry loop never blocks the event loop", and what blocks an event
    loop is a *call* to ``time.sleep``, not the presence of the word. The earlier form of this
    test banned the string ``"import time"`` from the module, which is a proxy for the real
    property and a leaky one in both directions: it fires on
    ``time.perf_counter()`` — which does not block, and which task 33.5's
    ``paper.order.submit_latency_ms`` / ``paper.fill.apply_latency_ms`` instrumentation needs a
    monotonic clock for (Requirements 26.6, 27.6) — while missing ``from time import sleep`` and
    ``import time as clock; clock.sleep(0)``, both of which do block and neither of which
    contains the banned string.

    Every alias the module could reach ``time.sleep`` through is resolved first: ``import time``
    and ``import time as t`` give the module aliases whose ``.sleep`` attribute is banned, and
    ``from time import sleep [as s]`` gives the bare names that are banned outright.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))

    module_aliases = {"time"}
    bare_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    module_aliases.add(alias.asname or "time")
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            for alias in node.names:
                if alias.name == "sleep":
                    bare_names.add(alias.asname or "sleep")

    offenders: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        blocking = (
            isinstance(func, ast.Attribute)
            and func.attr == "sleep"
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ) or (isinstance(func, ast.Name) and func.id in bare_names)
        if blocking:
            offenders.append(f"{node.lineno}: {ast.unparse(func)}(...)")
    return offenders


def test_the_retry_loop_uses_asyncio_sleep_and_never_time_sleep() -> None:
    """``time.sleep`` would block the event loop and stall every other request on this worker.

    Two claims, and the second is the load-bearing one. ``with_retries`` waits with
    ``asyncio.sleep``, and NOTHING in ``paper_simulator`` calls ``time.sleep`` at all — a
    module-wide ban, which is strictly stronger than banning it in the retry loop, because it also
    covers every helper the loop calls and every future edit that moves the wait into one.
    """
    import inspect

    source = inspect.getsource(sim.with_retries)
    assert "asyncio.sleep" in source, (
        "with_retries no longer waits with asyncio.sleep; a synchronous wait inside an async "
        "retry loop blocks the whole worker (Requirements 15.4, 16.10)"
    )

    offenders = _blocking_sleep_calls(sim)
    assert offenders == [], (
        "paper_simulator calls time.sleep, which blocks the event loop and stalls every other "
        f"request on this worker; the retry loop must wait with asyncio.sleep. Call site(s): "
        f"{offenders}"
    )


def test_apply_fill_gives_up_after_three_attempts_with_a_409() -> None:
    """Requirement 16.10: at most three attempts, then a concurrency conflict."""
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    supabase.after_select = _version_racer
    sleeps = _Sleeps()

    with pytest.raises(sim.PaperConcurrencyExhausted) as caught:
        _run_coroutine(
            sim.apply_fill(
                supabase,
                session,
                order,
                config=_config(fee_rate=Decimal("0"), slippage_rate=Decimal("0")),
                quantity="1",
                price="1000",
                fill_event_id="fill-1",
                filled_at=NOW,
                sleep=sleeps,
            )
        )

    error = caught.value
    assert error.code == PAPER_CONCURRENCY_CONFLICT
    assert error.http_status == 409
    assert error.details["attempts"] == 3
    assert error.details["operation"] == "apply_fill"
    # Three attempts means two waits, in the recorded order.
    assert sleeps.delays == [0.05, 0.10]
    # The money never moved: no balance event, no position, no equity point.
    assert supabase.balance_events == []
    assert supabase.positions == []
    assert supabase.equity_snapshots == []


def test_a_conflicting_retry_resumes_the_fill_row_rather_than_writing_a_second_one() -> None:
    """The one recovery this transport's write ordering admits.

    The fill row is the idempotency anchor and is written before the money moves, so a retry after
    a conflict on the account UPDATE finds it, recognises that no ledger row carries it, and
    resumes instead of inserting a second fill or skipping the movement. Three conflicting attempts
    therefore leave **one** fill row, not three.
    """
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    supabase.after_select = _version_racer

    with pytest.raises(sim.PaperConcurrencyExhausted):
        _run_coroutine(
            sim.apply_fill(
                supabase,
                session,
                order,
                config=_config(fee_rate=Decimal("0"), slippage_rate=Decimal("0")),
                quantity="1",
                price="1000",
                fill_event_id="fill-1",
                filled_at=NOW,
                sleep=_Sleeps(),
            )
        )

    assert len(supabase.fills) == 1


def test_submit_intents_lock_gives_up_with_the_phase_and_the_partial_write_named() -> None:
    """The residual gap, reported rather than hidden.

    The order is already durable at ``ACCEPTED`` when the lock's version-guarded UPDATE conflicts,
    and PostgREST offers no ``ROLLBACK`` for the two statements that committed. So the 409 names
    ``phase='ORDER_LOCK'`` and ``partial_write=True``, and the order stands with nothing locked -
    which is visible in the response instead of silent.
    """
    supabase, session, account_id = _seed()
    supabase.after_select = _version_racer
    sleeps = _Sleeps()

    with pytest.raises(sim.PaperConcurrencyExhausted) as caught:
        _submit(
            supabase,
            session,
            account_id,
            intent=_intent(order_type="limit", quantity="1", limit_price="1000"),
            sleep=sleeps,
        )

    error = caught.value
    assert error.code == PAPER_CONCURRENCY_CONFLICT
    assert error.details["phase"] == "ORDER_LOCK"
    assert error.details["partial_write"] is True
    assert sleeps.delays == [0.05, 0.10]
    assert supabase.orders[0]["order_state"] == PaperOrderState.ACCEPTED.value
    assert Decimal(supabase.accounts[0]["locked_balance"]) == Decimal("0")


def test_a_non_retryable_failure_is_raised_on_its_first_occurrence() -> None:
    """A retry is only safe where the statement definitively did not apply.

    A read that did not complete is not a conflict, so it is surfaced immediately rather than
    repeated - repeating an unknown-outcome write is how a fill gets applied twice.
    """
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    supabase.raise_on = {("select", repo.FILLS_TABLE)}
    sleeps = _Sleeps()

    with pytest.raises(repo.PaperPersistenceError):
        _fill(supabase, session, order, quantity="1", fill_event_id="fill-1")
    assert sleeps.delays == []


def test_the_retry_loop_is_written_once_and_shared() -> None:
    """Task 25.6: "the retry loop is written once and shared".

    Asserted on the source, because two copies of a retry policy are two policies and the one that
    gets edited is never the one that is running.
    """
    import inspect

    assert "with_retries(" in inspect.getsource(sim.submit_intent)
    assert "with_retries(" in inspect.getsource(sim.apply_fill)
    for name in ("submit_intent", "apply_fill", "_lock_for_order"):
        source = inspect.getsource(getattr(sim, name))
        assert "for attempt in range" not in source
        assert "RETRY_ATTEMPTS" not in source


# ══════════════════════════════════════════════════════════════════════════
#  TENANCY AND EXACTNESS (Requirements 18.1, 21.5)
# ══════════════════════════════════════════════════════════════════════════


def test_every_statement_the_two_write_paths_issue_is_scoped_to_the_owning_user() -> None:
    """Requirement 21.5: ``user_id`` is a predicate, so another tenant's row is never fetched."""
    supabase, session, account_id = _seed()
    _submit(
        supabase,
        session,
        account_id,
        intent=_intent(quantity="1", idempotency_key="key-1"),
        latest_event=_event(close="1000"),
    )

    # The migration probe is the one exception, and a deliberate one: it asks whether the RELATION
    # exists, selects one column and returns no tenant data, so scoping it would be meaningless.
    unscoped = [
        (q.table_name, q.op, q.cols)
        for q in supabase.statements
        if q.table_name.startswith("paper_")
        and "user_id" not in q.filtered_columns()
        and q.op != "insert"
        and q.cols != repo.PROBE_SELECT
    ]
    assert unscoped == []
    for query in supabase.statements:
        if "user_id" in query.filtered_columns():
            assert query.filter_value("user_id") == USER


def test_another_tenants_session_and_order_are_not_fillable() -> None:
    """The read is what enforces tenancy, so the row is never fetched rather than merely hidden."""
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    foreign = dict(session)
    foreign["user_id"] = OTHER_USER

    # The refusal comes from the very first read: ``user_id`` is a predicate on it, so the other
    # tenant's account is not fetched and there is no balance to compute against. It is a
    # persistence refusal rather than a validation one precisely because the row never arrived.
    with pytest.raises(repo.PaperPersistenceError):
        _fill(supabase, foreign, order, quantity="1")
    assert supabase.fills == []


def test_a_float_never_reaches_a_price_a_quantity_or_a_fee() -> None:
    """Requirement 18.1: a ``float`` is refused, not coerced, at every entrance."""
    config = _config()
    with pytest.raises(sim.InvalidOrderIntent):
        sim.OrderIntent.from_mapping(_intent(quantity=0.5))
    with pytest.raises(sim.InvalidOrderIntent):
        sim.OrderIntent.from_mapping(_intent(order_type="limit", limit_price=60000.0))
    with pytest.raises(Exception):
        sim.market_fill_price(1000.0, "buy", config)
    with pytest.raises(Exception):
        sim._event_decimal({"close": 1000.0}, "close")


def test_no_persisted_money_column_is_written_as_a_float() -> None:
    """The payloads themselves, not only the values that came back out."""
    supabase, session, account_id = _seed()
    _submit(
        supabase,
        session,
        account_id,
        config=_config(fee_rate=Decimal("0.001")),
        intent=_intent(quantity="1"),
        latest_event=_event(close="1000"),
    )
    for query in supabase.statements:
        for value in (query.payload or {}).values():
            assert not isinstance(value, float), f"{query.table_name}.{query.op} wrote a float"


def test_the_fill_event_id_of_a_market_order_is_derived_and_not_drawn() -> None:
    """A ``uuid4`` would make a replayed session write a second fill for the same order."""
    assert sim.market_fill_event_id("order-1") == "market-order-1"
    first = sim.resting_fill_event_id("order-1", _event(source_event_id="evt-1"))
    assert first == sim.resting_fill_event_id("order-1", _event(source_event_id="evt-1"))
    assert first != sim.resting_fill_event_id("order-1", _event(source_event_id="evt-2"))
    assert first != sim.resting_fill_event_id("order-2", _event(source_event_id="evt-1"))
    with pytest.raises(sim.InvalidOrderIntent):
        sim.resting_fill_event_id("order-1", {"symbol": SYMBOL})


def test_no_timestamp_is_read_from_a_clock() -> None:
    """Every instant this module writes was handed to it, which is what makes a replay identical."""
    supabase, session, account_id = _seed()
    order = _accepted_market_order(supabase, account_id, quantity="1")
    instant = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    outcome = _run_coroutine(
        sim.apply_fill(
            supabase,
            session,
            order,
            config=_config(fee_rate=Decimal("0"), slippage_rate=Decimal("0")),
            quantity="1",
            price="1000",
            fill_event_id="fill-1",
            filled_at=instant,
        )
    )

    assert outcome.applied is True
    assert supabase.fills[0]["filled_at"] == instant.isoformat()
    assert supabase.balance_events[0]["occurred_at"] == instant.isoformat()
    assert supabase.equity_snapshots[0]["taken_at"] == instant.isoformat()
    # And a fill with no instant is refused rather than silently stamped with now().
    with pytest.raises(sim.InvalidOrderIntent):
        _run_coroutine(
            sim.apply_fill(
                supabase,
                session,
                order,
                config=_config(),
                quantity="1",
                price="1000",
                fill_event_id="fill-2",
                filled_at=None,
            )
        )
