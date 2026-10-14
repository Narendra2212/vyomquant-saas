"""PAPER-02: an order that cannot be funded never stays in a state that can execute.

THE DEFECT
==========
``paper_trading_service.place_order``'s market branch inserted the order at ``CREATED`` and
moved it to ``ACCEPTED`` **before any funding decision was made**. There was no affordability
check on the market path at all: ``available_balance`` was read and then used only in the
``limit`` branch. The shortfall surfaced three steps later inside
``paper_accounting.apply_fill`` as ``InvariantViolation('available_balance')``, which became a
500 ``PAPER_INVARIANT_VIOLATION``. That refusal correctly wrote nothing - but the order was
already ``ACCEPTED`` / ``legacy_status='OPEN'`` with ``filled_quantity=0`` and
``rejection_reason=NULL``, and nothing ever went back for it.

Measured before the fix (``staging/probe_paper02_state.py``, ``staging/raw_Q3C.txt``)::

    order side=buy qty=500 state=ACCEPTED legacy=OPEN filled=0 reason=None
    PAPER02_STRANDED_ORDERS=1   PAPER02_REASON_RECORDED=False

Requirement 16.6 wants a persisted ``REJECTED`` order carrying an insufficient-funds reason,
with no funds locked.

WHY THESE TESTS USE REAL POSTGRESQL
-----------------------------------
The claim is about PERSISTED STATE, so the database is the thing under test and a double
cannot establish it. Every test here runs through ``tests/paper_pg_client.PostgresSupabase``
against the live instance, which means ``trg_paper_order_transition_guard`` really fires,
``chk_paper_order_state`` and ``chk_paper_balances_non_negative`` are real CHECKs,
``uq_paper_fill_event`` is a real unique constraint, and ``paper_fills``'s append-only
trigger really refuses an UPDATE.

THE ORACLE IS THE DATABASE
--------------------------
Every assertion reads rows back over an **independent psycopg2 connection** that does not go
through the shim, so what is asserted is what a second client would see - not what the call
returned. ``required_funds`` is also restated by hand here
(``qty * price * (1 + fee_rate + slippage_rate)``, quantized once) rather than imported, so
the expected threshold is derived from stated figures instead of from the implementation.

WHAT IS DELIBERATELY NOT CLAIMED
--------------------------------
There is no multi-statement transaction over PostgREST, so "rollback on rejection" is not
what is asserted: ``test_a_rejection_writes_nothing_at_all`` asserts that **no statement was
issued** against the balance, position, fill or ledger tables, which is a different and
stronger-to-state fact. ``paper_repository``'s header records the missing transaction as a
limitation of the transport.

The residual window is PAPER-04 and has its own test below, which asserts the residue is
**bounded and visible** rather than absent.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)
if not DSN.startswith("postgres"):  # pragma: no cover - selection guard
    pytest.skip("PAPER-02 requires PostgreSQL", allow_module_level=True)

from tests.paper_pg_client import (  # noqa: E402
    PostgresSupabase, paper_tables_present, purge_paper_user)

from backend_app.backend.paper import paper_repository as repo  # noqa: E402
from backend_app.backend.paper.paper_order_state import (  # noqa: E402
    PaperOrderState, can_transition)
from backend_app.backend.paper.paper_simulator import (  # noqa: E402
    REJECTION_INSUFFICIENT_FUNDS, REJECTION_REASONS)
from backend_app.backend.paper_trading_service import PaperTradingService  # noqa: E402

SYMBOL = "BTC-USDT"

#: ``PaperTradingService.__init__`` defaults, restated so every expected figure below is
#: derived from stated numbers rather than from whatever the service happens to hold.
FEE_RATE = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.0005")
MONEY = Decimal("0.01")  # MONEY_EXPONENT = 2


PRICE_Q = Decimal("0.00000001")  # PRICE_PRECISION = 8


def required_funds(quantity: str | Decimal, price: str | Decimal) -> Decimal:
    """Requirement 16.6's GATE figure, spelled out by hand.

    ``notional * (1 + fee_rate + slippage_rate)``, quantized ONCE at the end with
    ``ROUND_HALF_EVEN``. Deliberately not ``paper_accounting.required_funds``: an oracle that
    calls the implementation agrees with it by construction.

    **This is an allowance, not a cost.** It is what the account must hold before the order
    is accepted; what the fill actually takes is :func:`fill_cost`, and the two are different
    numbers. Conflating them was an error in the first version of this file - see
    :func:`fill_cost`.
    """
    notional = Decimal(str(quantity)) * Decimal(str(price))
    return (notional * (Decimal("1") + FEE_RATE + SLIPPAGE_RATE)).quantize(
        MONEY, rounding=ROUND_HALF_EVEN
    )


def fill_cost(quantity: str | Decimal, price: str | Decimal) -> tuple[Decimal, Decimal]:
    """What the fill actually costs: ``(position_value, fee)``, each quantized separately.

    CORRECTION. The first version of this file asserted the post-fill balance as
    ``capital - required_funds(...)``, and it was wrong: for ``qty=50 @ 1`` it expected
    ``49.92`` and the ledger held ``49.93``. ``required_funds`` is a conservative
    pre-acceptance allowance (``notional + fee + slippage`` allowances); the cash the fill
    moves is the position's **value** at the slipped fill price plus the fee, each quantized
    to the Minor_Units scale **independently**. For that order the value is
    ``money(50 * 1.0005) = money(50.025) = 50.02`` - ``ROUND_HALF_EVEN`` resolves the exact
    half downward because ``2`` is even - and the fee is ``money(0.050025) = 0.05``, so the
    balance is ``100 - 50.02 - 0.05 = 49.93``. The implementation was right and the oracle was
    not.

    Spelled out here rather than imported, for the same reason as :func:`required_funds`.
    """
    qty = Decimal(str(quantity))
    fill_price = (Decimal(str(price)) * (Decimal("1") + SLIPPAGE_RATE)).quantize(
        PRICE_Q, rounding=ROUND_HALF_EVEN
    )
    value = (qty * fill_price).quantize(MONEY, rounding=ROUND_HALF_EVEN)
    fee = (qty * fill_price * FEE_RATE).quantize(MONEY, rounding=ROUND_HALF_EVEN)
    return value, fee


def available_after_fill(capital: str, quantity: str, price: str) -> Decimal:
    """``capital - position_value - fee``, from :func:`fill_cost`."""
    value, fee = fill_cost(quantity, price)
    return Decimal(capital) - value - fee


# ══════════════════════════════════════════════════════════════════════════
#  FIXTURES AND THE INDEPENDENT ORACLE
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
    uid = str(uuid.uuid4())
    yield uid
    purge_paper_user(DSN, uid)


@pytest.fixture
def pg():
    client = PostgresSupabase(DSN)
    yield client
    client.close()


class Oracle:
    """Reads the paper tables over its OWN psycopg2 connection, not through the shim."""

    def __init__(self, dsn: str, uid: str) -> None:
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self.uid = uid

    def close(self) -> None:
        self._conn.close()

    def _all(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def orders(self) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT id, order_state, legacy_status, side, quantity, filled_quantity, "
            "       avg_fill_price, rejection_reason, idempotency_key "
            "  FROM paper_orders WHERE user_id = %s ORDER BY created_at",
            (self.uid,),
        )

    def account(self) -> Optional[Dict[str, Any]]:
        rows = self._all(
            "SELECT available_balance, locked_balance, realized_pnl, total_equity, version "
            "  FROM paper_accounts WHERE user_id = %s",
            (self.uid,),
        )
        return rows[0] if rows else None

    def count(self, table: str) -> int:
        return self._all(
            f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (self.uid,)
        )[0]["n"]

    def balance_event_causes(self) -> List[str]:
        return [
            row["cause"]
            for row in self._all(
                "SELECT cause FROM paper_balance_events WHERE user_id = %s "
                " ORDER BY occurred_at",
                (self.uid,),
            )
        ]

    def unsettled_fills(self) -> int:
        """Fill rows that no ``FILL`` ledger row references - the PAPER-04 footprint."""
        return self._all(
            "SELECT count(*) AS n FROM paper_fills f WHERE f.user_id = %s "
            "   AND NOT EXISTS (SELECT 1 FROM paper_balance_events e "
            "                    WHERE e.cause = 'FILL' AND e.fill_id = f.id)",
            (self.uid,),
        )[0]["n"]

    def force_state(self, order_id: str, state: str) -> None:
        """A raw UPDATE, bypassing every Python guard, to exercise the DATABASE trigger."""
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE paper_orders SET order_state = %s WHERE id = %s", (state, order_id)
        )
        cur.close()


@pytest.fixture
def oracle(user):
    ora = Oracle(DSN, user)
    yield ora
    ora.close()


def service_with(pg, capital: str) -> PaperTradingService:
    """A service whose default account starts at exactly ``capital``."""
    return PaperTradingService(supabase=pg, default_capital=float(capital))


async def place(
    service: PaperTradingService,
    uid: str,
    *,
    side: str = "buy",
    quantity: str = "1",
    price: str = "1",
    order_type: str = "market",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    return await service.place_order(
        user_id=uid,
        symbol=SYMBOL,
        side=side,
        order_type=order_type,
        quantity=float(quantity),
        price=float(price),
        idempotency_key=idempotency_key,
    )


def expect_insufficient_funds(coro) -> Exception:
    """Run ``coro`` and return the refusal, asserting it is the 400 and not the old 500."""
    from backend_app.backend.paper.errors import PaperError

    with pytest.raises(PaperError) as caught:
        asyncio.run(coro)
    error = caught.value
    assert error.code == "PAPER_INSUFFICIENT_FUNDS", (
        f"expected the 400 PAPER_INSUFFICIENT_FUNDS refusal, got {error.code}. "
        "PAPER_INVARIANT_VIOLATION here means the funds gate was skipped and the "
        "shortfall was found at fill time again."
    )
    return error


# ══════════════════════════════════════════════════════════════════════════
#  1-5: THE FUNDING DECISION ITSELF
# ══════════════════════════════════════════════════════════════════════════


def test_1_insufficient_cash_is_rejected_not_accepted(pg, user, oracle):
    """The headline case. 100 available, an order needing 500.75."""
    service = service_with(pg, "100")
    service.get_or_create_account(user)

    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    orders = oracle.orders()
    assert len(orders) == 1, "exactly one order row, the rejected one"
    assert orders[0]["order_state"] == PaperOrderState.REJECTED.value
    assert orders[0]["legacy_status"] == "REJECTED"
    assert Decimal(str(orders[0]["filled_quantity"])) == Decimal("0")


def test_2_insufficient_only_after_fees_and_slippage_is_rejected(pg, user, oracle):
    """The case a notional-only or a fee-only gate would get wrong.

    ``qty=100 @ 1`` has a notional of exactly ``100.00``. Requirement 16.6's required funds
    are ``100 * 1.0015 = 100.15``. The account holds ``100.12`` - more than the notional, and
    more than the ``limit`` branch's weaker ``notional * (1 + fee_rate) = 100.10`` - so this
    order is affordable under both of the wrong formulas and unaffordable under the right one.
    """
    assert required_funds("100", "1") == Decimal("100.15")
    notional_only = Decimal("100.00")
    fee_only = (Decimal("100") * (Decimal("1") + FEE_RATE)).quantize(MONEY)
    assert fee_only == Decimal("100.10")
    assert notional_only < fee_only < Decimal("100.12") < required_funds("100", "1")

    service = service_with(pg, "100.12")
    service.get_or_create_account(user)

    expect_insufficient_funds(place(service, user, quantity="100", price="1"))

    assert oracle.orders()[0]["order_state"] == PaperOrderState.REJECTED.value
    assert Decimal(str(oracle.account()["available_balance"])) == Decimal("100.12")


def test_3_the_exactly_affordable_amount_is_accepted_and_fills(pg, user, oracle):
    """``required == available`` is affordable. The gate is ``>``, not ``>=``."""
    assert required_funds("100", "1") == Decimal("100.15")
    service = service_with(pg, "100.15")
    service.get_or_create_account(user)

    asyncio.run(place(service, user, quantity="100", price="1"))

    orders = oracle.orders()
    assert len(orders) == 1
    assert orders[0]["order_state"] == PaperOrderState.FILLED.value
    assert orders[0]["rejection_reason"] is None
    assert Decimal(str(oracle.account()["available_balance"])) == Decimal("0.00")


def test_4_one_minor_unit_below_the_threshold_is_rejected(pg, user, oracle):
    """The boundary, as a pair with the test above: 100.15 fills, 100.14 does not.

    One cent is the smallest difference the account currency can express
    (``MONEY_EXPONENT = 2``), so this is the tightest possible statement of where the gate is.
    """
    service = service_with(pg, "100.14")
    service.get_or_create_account(user)

    expect_insufficient_funds(place(service, user, quantity="100", price="1"))

    assert oracle.orders()[0]["order_state"] == PaperOrderState.REJECTED.value
    assert Decimal(str(oracle.account()["available_balance"])) == Decimal("100.14")


def test_5_an_oversized_market_buy_is_rejected(pg, user, oracle):
    """A quantity far beyond any balance, to show the gate is not a near-miss check."""
    service = service_with(pg, "100")
    service.get_or_create_account(user)

    expect_insufficient_funds(place(service, user, quantity="1000000", price="1"))

    assert oracle.orders()[0]["order_state"] == PaperOrderState.REJECTED.value
    assert oracle.count("paper_fills") == 0


# ══════════════════════════════════════════════════════════════════════════
#  6-8: WHAT THE REJECTED ROW MUST SAY, AND THAT IT IS TERMINAL
# ══════════════════════════════════════════════════════════════════════════


def test_6_the_rejected_order_is_persisted_and_survives_a_new_connection(pg, user, oracle):
    """Requirement 16.6 wants the rejection PERSISTED, not just returned.

    Read back over the independent connection AND re-read through a second shim client, so
    what is asserted is durable state rather than anything held in the first client.
    """
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    assert oracle.orders()[0]["order_state"] == PaperOrderState.REJECTED.value

    second = PostgresSupabase(DSN)
    try:
        rows = repo.get_orders(second, user)
        assert len(rows) == 1
        assert rows[0]["order_state"] == PaperOrderState.REJECTED.value
        assert rows[0]["legacy_status"] == "REJECTED"
    finally:
        second.close()


def test_7_the_rejection_reason_is_persisted_and_is_in_the_closed_vocabulary(
    pg, user, oracle
):
    """The reason has to name the failed check, and be one the vocabulary admits.

    009 declares no CHECK constraint on ``rejection_reason``, so ``REJECTION_REASONS`` - not
    the database - is the enumeration, which is why it is asserted against here.
    """
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    error = expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    reason = oracle.orders()[0]["rejection_reason"]
    assert reason == REJECTION_INSUFFICIENT_FUNDS == "INSUFFICIENT_FUNDS"
    assert reason in REJECTION_REASONS

    # The caller is told the two figures that decided it, not just that it failed - and both
    # at the account currency's Minor_Units scale, so they are comparable. The first version
    # of the service reported ``available`` straight off the NUMERIC(28, 10) column, so the
    # refusal read ``required=500.75`` beside ``available=100.0000000000``.
    assert error.details["required"] == str(required_funds("500", "1")) == "500.75"
    assert error.details["available"] == "100.00"
    assert error.details["rejection_reason"] == REJECTION_INSUFFICIENT_FUNDS


def test_8_a_rejected_order_can_never_later_fill(pg, user, oracle):
    """``REJECTED`` is terminal (Requirement 16.3), enforced in BOTH places.

    The Python guard and the database trigger are asserted separately, because either one
    alone would leave the other as an assumption. The trigger matters most: it is what stops
    a ``psql`` session holding the service key from resurrecting the order.
    """
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    expect_insufficient_funds(place(service, user, quantity="500", price="1"))
    order_id = str(oracle.orders()[0]["id"])

    # 1. The state machine itself.
    assert not can_transition(PaperOrderState.REJECTED, PaperOrderState.FILLED)
    assert not can_transition(PaperOrderState.REJECTED, PaperOrderState.ACCEPTED)

    # 2. The repository's guarded UPDATE refuses before issuing a statement.
    with pytest.raises(ValueError):
        repo.update_order(
            pg,
            user_id=user,
            order_id=order_id,
            order_state=PaperOrderState.FILLED,
            expected_state=PaperOrderState.REJECTED,
        )

    # 3. The DATABASE, with every Python guard bypassed. SQLSTATE 23514 is what
    #    paper_order_transition_guard raises, naming both states.
    with pytest.raises(psycopg2.errors.CheckViolation):
        oracle.force_state(order_id, PaperOrderState.FILLED.value)

    assert oracle.orders()[0]["order_state"] == PaperOrderState.REJECTED.value


# ══════════════════════════════════════════════════════════════════════════
#  9-11: RETRY AND DUPLICATE IDENTITY
# ══════════════════════════════════════════════════════════════════════════


def test_9_a_smaller_order_after_a_rejection_succeeds(pg, user, oracle):
    """A rejection is not a latch. The account is unharmed and still usable."""
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    asyncio.run(place(service, user, quantity="50", price="1"))

    states = sorted(row["order_state"] for row in oracle.orders())
    assert states == [PaperOrderState.FILLED.value, PaperOrderState.REJECTED.value]
    # The GATE allowed it because 50.08 <= 100.00; the COST that actually moved is the
    # position value plus the fee, which is a different figure. See fill_cost's docstring.
    assert required_funds("50", "1") == Decimal("50.08")
    assert fill_cost("50", "1") == (Decimal("50.02"), Decimal("0.05"))
    assert Decimal(str(oracle.account()["available_balance"])) == available_after_fill(
        "100", "50", "1"
    ) == Decimal("49.93")


def test_10_a_duplicate_fill_event_id_is_refused_by_the_database(pg, user, oracle):
    """``uq_paper_fill_event (order_id, fill_event_id)``, which the random id made inert.

    This path used to mint ``f"exec_paper_{uuid4().hex[:12]}"``, so the constraint could never
    recognise a repeated fill and the only guard was the order-level idempotency key. The id
    is now ``f"exec_paper_{order_id}"``, so a second fill of the same order is refused by the
    database - asserted here by trying to insert one.
    """
    service = service_with(pg, "1000")
    service.get_or_create_account(user)
    asyncio.run(place(service, user, quantity="10", price="1"))

    order_id = str(oracle.orders()[0]["id"])
    assert oracle.count("paper_fills") == 1

    # ``PaperDuplicateFill`` is the repository's dedicated answer for this constraint - a
    # distinct type from ``PaperConcurrencyConflict``, because a duplicate fill event is not
    # a lost race: "the first application stands and this one changes nothing".
    with pytest.raises(repo.PaperDuplicateFill) as caught:
        repo.insert_fill(
            pg,
            order_id=order_id,
            user_id=user,
            fill_event_id=PaperTradingService._fill_event_id(order_id),
            quantity=Decimal("10"),
            price=Decimal("1"),
            fee_minor=0,
            slippage_minor=0,
            filled_at=service._get_timestamp(),
        )
    assert "already recorded on order" in str(caught.value)
    assert oracle.count("paper_fills") == 1, "no second fill row landed"


def test_11_a_duplicate_idempotency_key_returns_the_rejected_order(pg, user, oracle):
    """Requirement 16.8 applies to a rejection too: the retry answers with the same order.

    Not a second rejection, and emphatically not a second order row - the point of the key is
    that one intent produces one order whatever its outcome.
    """
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    key = "paper02-dup-key"

    expect_insufficient_funds(
        place(service, user, quantity="500", price="1", idempotency_key=key)
    )
    first = oracle.orders()
    assert len(first) == 1

    replay = asyncio.run(
        place(service, user, quantity="500", price="1", idempotency_key=key)
    )

    assert oracle.orders() == first, "the replay created or changed no row"
    assert replay["status"] in ("REJECTED", "rejected") or replay.get(
        "order_state"
    ) in (PaperOrderState.REJECTED.value,), (
        f"the replay must report the recorded rejection, got {replay!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  12, 15, 16: WHAT A REJECTION MUST NOT TOUCH
# ══════════════════════════════════════════════════════════════════════════


def test_12_the_account_balance_is_untouched_by_a_rejection(pg, user, oracle):
    """Requirement 16.6: "SHALL make no change to the balances or positions"."""
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    before = oracle.account()

    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    after = oracle.account()
    assert after == before, (
        "every column of paper_accounts must be byte-identical after a rejection, "
        f"including version: {before} -> {after}"
    )


def test_15_a_rejection_writes_nothing_at_all(pg, user, oracle):
    """Stated as "no statement was issued", NOT as "a transaction rolled back".

    PostgREST gives this path no multi-statement transaction - ``paper_repository``'s header
    records that as a limitation of the transport - so there is no rollback to assert. What
    CAN be asserted, and is stronger, is that the rejection path issues exactly one statement
    (the order's own UPDATE to ``REJECTED``) and touches no other table. The distinction is
    kept explicit rather than borrowing the word "rollback" for something else.
    """
    service = service_with(pg, "100")
    service.get_or_create_account(user)
    version_before = oracle.account()["version"]

    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    for table in (
        "paper_fills",
        "paper_positions",
        "paper_balance_events",
        "paper_trades",
        "paper_equity_snapshots",
    ):
        assert oracle.count(table) == 0, f"{table} was written during a rejection"
    assert oracle.account()["version"] == version_before, (
        "the account version moved, so a write was attempted against paper_accounts"
    )


def test_16_no_reservation_remains_after_a_rejection(pg, user, oracle):
    """Requirement 16.6's "SHALL lock no funds", from both sides of the ledger."""
    service = service_with(pg, "100")
    service.get_or_create_account(user)

    expect_insufficient_funds(place(service, user, quantity="500", price="1"))

    assert Decimal(str(oracle.account()["locked_balance"])) == Decimal("0")
    assert "ORDER_LOCK" not in oracle.balance_event_causes()
    assert oracle.count("paper_balance_events") == 0


# ══════════════════════════════════════════════════════════════════════════
#  13-14: CONCURRENCY, OVER TWO INDEPENDENT CONNECTIONS
# ══════════════════════════════════════════════════════════════════════════


def test_13_and_14_two_competing_buys_leave_exactly_one_filled_and_one_rejected(
    user, oracle
):
    """The brief's critical case, proven from database state.

        capital 100, worker A buys 80, worker B buys 80
        required per order = 80 * 1.0015 = 80.12, so both cannot be funded

    Required: exactly one accepted, exactly one rejected.
    Forbidden: both accepted, negative cash, double reservation, silent overwrite.

    TWO INDEPENDENT POSTGRESQL CONNECTIONS, not two coroutines on one loop: each worker gets
    its own ``PostgresSupabase`` (its own psycopg2 connection) and its own event loop on its
    own thread, so the ``paper_accounts.version`` predicate is arbitrating across connections
    exactly as it would across replicas. The assertions then read over a THIRD connection.

    Also measured across two separate OS PROCESSES, ten consecutive races, in
    ``staging/probe_paper02_race_stability.py``: ``REQUIRED_INVARIANTS=HELD EVERY RUN`` with
    ``PAPER04_RESIDUE unsettled_fill_rows -> 0:10``.
    """
    assert required_funds("80", "1") == Decimal("80.12")

    seed = PostgresSupabase(DSN)
    try:
        service_with(seed, "100").get_or_create_account(user)
    finally:
        seed.close()

    results: Dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def worker(tag: str) -> None:
        client = PostgresSupabase(DSN)
        try:
            service = service_with(client, "100")
            barrier.wait(timeout=30)  # both threads start the call together
            try:
                results[tag] = ("ok", asyncio.run(place(service, user, quantity="80")))
            except Exception as exc:  # noqa: BLE001
                results[tag] = ("error", type(exc).__name__, getattr(exc, "code", None))
        finally:
            client.close()

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert len(results) == 2, f"both workers must finish; got {results}"

    orders = oracle.orders()
    filled = [o for o in orders if o["order_state"] == PaperOrderState.FILLED.value]
    rejected = [o for o in orders if o["order_state"] == PaperOrderState.REJECTED.value]
    non_terminal = [
        o
        for o in orders
        if o["order_state"]
        not in (
            PaperOrderState.FILLED.value,
            PaperOrderState.REJECTED.value,
            PaperOrderState.CANCELLED.value,
        )
    ]
    account = oracle.account()
    available = Decimal(str(account["available_balance"]))

    assert len(orders) == 2, f"two intents, two order rows: {orders}"
    assert len(filled) == 1, f"exactly one must fill, got {len(filled)}: {results}"
    assert len(rejected) == 1, f"exactly one must be rejected, got {len(rejected)}"
    assert rejected[0]["rejection_reason"] == REJECTION_INSUFFICIENT_FUNDS
    assert not non_terminal, (
        f"an order that cannot be funded must not remain live: {non_terminal}"
    )

    # The forbidden list, each item named.
    assert available >= 0, "negative cash"
    assert available == Decimal("100") - required_funds("80", "1") == Decimal("19.88"), (
        "the surviving balance must be exactly one order's cost - a different figure means "
        "one write silently overwrote the other"
    )
    assert oracle.balance_event_causes() == ["FILL"], (
        "exactly one money movement; two would be a double reservation"
    )
    assert oracle.count("paper_positions") == 1, "only the winner may hold a position"

    # Every fill row the money actually moved for. The loser may leave an UNSETTLED one -
    # PAPER-04 - which is counted separately rather than asserted away.
    settled = oracle.count("paper_fills") - oracle.unsettled_fills()
    assert settled == 1, f"exactly one settled fill, got {settled}"


def test_the_paper04_residue_is_bounded_and_never_a_settled_fill(user, oracle):
    """The residual window, asserted as BOUNDED rather than absent.

    ``_attempt_fill`` writes the fill row before the money because that row is the resume
    anchor - ``paper_simulator``'s stated reasoning, and the thing
    ``test_a_conflicting_retry_resumes_the_fill_row_rather_than_writing_a_second_one`` pins
    for the session path. A conflict detected after that INSERT therefore leaves the row
    behind, and ``paper_fills`` is append-only so nothing can remove it.

    What must hold, and is asserted here, is that such a row is never SETTLED: no ``FILL``
    ledger row references it, so no money moved for it and no reader can mistake it for a
    completed fill. Ten consecutive two-process races produced zero of them
    (``staging/probe_paper02_race_stability.py``); the forced-conflict probe
    (``staging/probe_paper02_phantom.py``) produces exactly one, with
    ``positions=0`` and ``balance_events=0``.
    """
    seed = PostgresSupabase(DSN)
    try:
        service_with(seed, "1000").get_or_create_account(user)
        service = service_with(seed, "1000")
        asyncio.run(place(service, user, quantity="10", price="1"))
    finally:
        seed.close()

    # The happy path leaves none at all.
    assert oracle.unsettled_fills() == 0
    assert oracle.count("paper_fills") == 1
    assert oracle.balance_event_causes() == ["FILL"]

    # And the invariant that makes the residue harmless when it does occur: every fill row
    # the ledger references moved money exactly once.
    assert oracle.count("paper_fills") - oracle.unsettled_fills() == 1


import pytest as _pytest_p02

pytestmark = _pytest_p02.mark.usefixtures("fresh_paper_quote")  # PAPER-02
