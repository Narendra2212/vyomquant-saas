"""A live order may not commit more notional than the account can fund.

WHAT THIS GUARDS
----------------
``ExecutionEngine.execute_with_idempotency`` is the execution gateway — the funnel every
live order is supposed to pass through. Its checks are: execution environment, caller
source, global kill switch, strategy existence, idempotency, and the claim.

UPDATE (TRADE-05 remediation): the sentence below is no longer true. The gateway now
REQUIRES an approved risk verdict for a live order and refuses without one, so risk is a
property of the gateway rather than of one caller. The historical statement is kept
because it is what this file was written against, and `_live_engine` now satisfies the
new precondition explicitly so these tests still measure the notional ceiling.

It contained
**no risk evaluation at all**; the pre-launch audit established this by searching the
gateway and ``_execute_trade_internal`` for ``ExecutionGuard``, ``RiskEngine``,
``validate_trade`` and ``validate_trade_request`` and finding none.

Risk is enforced one level up, in ``BotRunner._inner_loop``. That is the only production
caller, so there was no live bypass — but it makes "this order passed risk" a property of
one caller rather than of the gateway, while ``execute_trade`` hardcodes
``source="bot_runner"``, so any new caller inherits the authorisation without the check.
``backend/execution_guard.py`` calls itself "the FINAL CONTROL LAYER that validates EVERY
trade before execution" and is wired only to the two cancel endpoints.

This suite pins the one bound the gateway now owns itself: an order may not exceed
``equity x max_leverage``. It is deliberately the weakest useful bound, because a bound
that could refuse a correctly sized order would be unsafe to add to the live path. It
catches the category that loses an account in a single order — a sizing bug (the audit
found ATR sizing returning 125x equity on a low-volatility asset), a decimal shift, a
replayed signal, or a base/quote unit mix-up.

It is NOT a risk engine and these tests do not claim it is. Daily-loss limits, open
position counts and risk-per-trade remain unenforced in the gateway; that is recorded as
an outstanding item in the audit, not as something these tests cover.
"""

import asyncio
from decimal import Decimal

import pytest

from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.core.execution_engine import ExecutionEngine


class _StubOrderResult:
    """The shape ``_execute_trade_internal`` reads off a placement result."""

    success = True
    exchange_order_id = "VENUE-STUB-1"
    status = "pending"
    filled_size = Decimal("0")
    remaining_size = Decimal("0")
    avg_price = None
    error_message = None
    raw_response = {"id": "VENUE-STUB-1", "status": "open"}


class SpyExecutor:
    """Stands in for CCXTExchangeExecutor and records whether it was reached.

    It RETURNS rather than raises. The live branch of ``_execute_trade_internal`` wraps
    the whole placement in ``except Exception`` and converts it into a
    ``(False, "Exchange executor exception: ...")`` tuple, so an exception raised here
    would be indistinguishable from the refusal under test — the assertion has to be
    "was this object reached", recorded as data.
    """

    def __init__(self):
        self.orders = []
        self._current_execution_id = None
        self._current_validation_token = None

    async def place_order(self, **kw):
        self.orders.append(kw)
        return _StubOrderResult()


def _live_engine(equity, executor=None, **kw):
    engine = ExecutionEngine(
        portfolio_state={"total_equity": equity},
        exchange_executor=executor if executor is not None else SpyExecutor(),
        execution_environment=ExecutionEnvironment.LIVE,
        **kw,
    )
    # TRADE-05. These tests drive `_execute_trade_internal` DIRECTLY, so they skip
    # `execute_with_idempotency` -- which is where the risk verdict is now recorded.
    # Since the gateway refuses a live order whose `_live_risk_approved` is not True, an
    # unset verdict would refuse every order here and this file would stop measuring the
    # notional ceiling it exists to measure: every assertion would pass for the wrong
    # reason.
    #
    # Satisfying the precondition explicitly keeps the subject of these tests the
    # CEILING. That the refusal exists at all is covered separately, and from the public
    # entry point, by tests/test_trade04_live_order_gate_at_venue_boundary.py.
    engine._live_risk_approved = True
    engine._live_risk_reason = "risk approved (test precondition)"
    engine._live_tenant_id = "00000000-0000-0000-0000-000000000001"
    engine._live_strategy_id = "strategy-under-test"
    return engine


def _attempt(engine, size, price, execution_id="exec_test0000001"):
    return asyncio.run(
        engine._execute_trade_internal(
            symbol="BTC/USDT",
            side="buy",
            size=Decimal(str(size)),
            price=Decimal(str(price)),
            execution_id=execution_id,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# The bound
# ─────────────────────────────────────────────────────────────────────────────

def test_an_order_exceeding_account_equity_is_refused():
    """THE DEFECT, with the audit's worked sizing example: 25 BTC at 50,000 is
    1,250,000 of notional against 10,000 of equity."""
    engine = _live_engine(10000.0)

    ok, reason = _attempt(engine, size="25", price="50000")

    assert ok is False
    assert "notional" in reason.lower()
    assert engine.exchange_executor.orders == [], "the order reached the executor"


def test_a_decimal_shift_is_caught():
    """0.1 BTC mistyped as 1.0 BTC at 50,000 against 10,000 of equity."""
    engine = _live_engine(10000.0)

    ok, reason = _attempt(engine, size="1.0", price="50000")

    assert ok is False
    assert "exceeds the maximum fundable notional" in reason


def test_an_order_inside_equity_is_not_refused_by_this_bound():
    """The bound must not false-refuse. 0.1 BTC at 50,000 is 5,000 against 10,000 —
    fundable, so it must pass this check and reach the executor."""
    engine = _live_engine(10000.0)

    ok, _ = _attempt(engine, size="0.1", price="50000")

    assert ok is True
    assert len(engine.exchange_executor.orders) == 1, (
        "a fundable order was refused by the notional ceiling"
    )


def test_an_order_exactly_at_the_ceiling_is_permitted():
    """The boundary belongs to the trader: spending the whole account is fundable."""
    engine = _live_engine(10000.0)

    ok, _ = _attempt(engine, size="0.2", price="50000")  # exactly 10,000

    assert ok is True
    assert len(engine.exchange_executor.orders) == 1


def test_one_cent_over_the_ceiling_is_refused():
    """Pins the comparison as strict rather than approximate."""
    engine = _live_engine(10000.0)

    ok, reason = _attempt(engine, size="0.2000001", price="50000")
    assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Leverage
# ─────────────────────────────────────────────────────────────────────────────

def test_configured_leverage_raises_the_ceiling():
    """An engine told it may use leverage must be able to. 3x on 10,000 funds 30,000."""
    engine = _live_engine(10000.0)
    engine.max_leverage = 3

    ok, _ = _attempt(engine, size="0.5", price="50000")  # 25,000 <= 30,000

    assert ok is True
    assert len(engine.exchange_executor.orders) == 1


def test_leverage_does_not_make_the_ceiling_unbounded():
    engine = _live_engine(10000.0)
    engine.max_leverage = 3

    ok, _ = _attempt(engine, size="1.0", price="50000")  # 50,000 > 30,000
    assert ok is False


def test_the_default_leverage_is_one():
    """Leverage is configured, validated and reported elsewhere in this codebase but
    never applied, so the gateway must not assume any."""
    engine = _live_engine(10000.0)
    assert Decimal(str(getattr(engine, "max_leverage", 1) or 1)) == Decimal("1")


# ─────────────────────────────────────────────────────────────────────────────
# Fail closed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("equity", [0.0, -100.0])
def test_a_non_positive_equity_refuses_rather_than_permitting(equity):
    """If the account is not worth anything, no order is fundable."""
    engine = _live_engine(equity)

    ok, reason = _attempt(engine, size="0.001", price="50000")

    assert ok is False
    assert "could not be established" in reason or "notional" in reason.lower()


def test_an_unreadable_equity_refuses_rather_than_permitting():
    """An engine that cannot say what the account is worth cannot show an order is
    fundable, and the safe answer to that is refusal, not submission."""
    engine = _live_engine(10000.0)
    engine.current_equity = "not-a-number"

    ok, reason = _attempt(engine, size="0.001", price="50000")

    assert ok is False
    assert "could not be established" in reason


# ─────────────────────────────────────────────────────────────────────────────
# The bound sits ahead of the exchange call, not after it
# ─────────────────────────────────────────────────────────────────────────────

def test_the_refusal_happens_before_the_validation_token_is_minted():
    """A refused order must not leave a usable anti-bypass token on the executor, and
    must not have consumed anything on the way to being refused."""
    executor = SpyExecutor()
    engine = _live_engine(10000.0, executor=executor)

    ok, _ = _attempt(engine, size="25", price="50000")

    assert ok is False
    assert executor._current_validation_token is None
    assert executor._current_execution_id is None


def test_the_paper_branch_is_unaffected_by_the_live_bound():
    """The ceiling guards the LIVE branch. A paper engine has its own capital check in
    ``open_position`` and must keep behaving as it did."""
    engine = ExecutionEngine(
        portfolio_state={"total_equity": 10000.0},
        execution_environment=ExecutionEnvironment.PAPER,
    )

    ok, message = asyncio.run(
        engine._execute_trade_internal(
            symbol="BTCUSDT",
            side="buy",
            size=Decimal("0.1"),
            price=Decimal("50000"),
            execution_id="exec_paper000001",
        )
    )

    # Reaches the paper simulator rather than the ceiling check; whether it fills is the
    # paper engine's own business, but it must not be refused with the live reason.
    assert "Risk gateway" not in str(message)


# ─────────────────────────────────────────────────────────────────────────────
# The latent bypass is sealed
# ─────────────────────────────────────────────────────────────────────────────

def test_the_unrisked_submission_path_refuses_to_place_a_live_order():
    """``idempotent_exchange_submission.submit_order_idempotent`` built a LIVE engine and
    called ``execute_trade`` — which hardcodes ``source="bot_runner"`` — with no risk
    evaluation anywhere in the function. It had no callers; it is now sealed."""
    from datetime import datetime, timezone

    from backend_app.backend.distributed_execution.idempotent_exchange_submission import \
        IdempotentExchangeSubmission

    sub = IdempotentExchangeSubmission()

    class FakeRedis:
        def __init__(self):
            self.store = {}

        async def set(self, key, value, ex=None, nx=False, **kw):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        async def setex(self, key, ttl, value):
            self.store[key] = value
            return True

        async def exists(self, key):
            return key in self.store

        async def delete(self, key):
            self.store.pop(key, None)
            return True

    sub.redis = FakeRedis()

    result = asyncio.run(
        sub.submit_order_idempotent(
            # A real UUID: the function parses the tenant id before it reaches the
            # refusal, so a non-UUID string fails earlier for an unrelated reason and
            # the test would pass for the wrong one.
            tenant_id="11111111-2222-3333-4444-555555555555",
            strategy_id="strat-1",
            signal_id="sig-1",
            timestamp=datetime.now(timezone.utc),
            order_data={
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": "0.01",
                "price": "50000",
                "portfolio_state": {"total_equity": "10000"},
            },
            exchange_client=object(),
        )
    )

    assert result["success"] is False
    assert result["error"] == "RISK_EVALUATION_REQUIRED"
    assert result["exchange_order_id"] is None
