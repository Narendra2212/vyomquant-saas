"""TRADE-04 / TRADE-05: no unauthorized live exchange write.

WHY THIS FILE IS SHAPED THIS WAY
================================
The pre-launch audit concluded live trading was OFF because
``ExecutionFlags.LIVE_TRADING_ENABLED`` is a hardcoded ``False``. That conclusion was
produced by a script that invoked exactly one function --
``SafetyMonitor.check_execution_allowed("live_order")`` -- which an AST trace later showed
is **not on the path that places an order**. The test proved a control that cannot refuse
an order.

So this file does not test the gate function in isolation. Every assertion below is about
**whether a fake exchange client's ``create_order`` was called**, driven through the real
code:

    ExecutionEngine.execute_with_idempotency
      -> ExecutionEngine._execute_trade_internal        (live branch)
        -> CCXTExchangeExecutor.place_order             (the REAL method body)
          -> self._exchange.create_order               (the recorder)

``CCXTExchangeExecutor`` is instantiated with ``object.__new__`` and its attributes set
directly, so no constructor side effect or network call occurs **and the method under test
is the real one**. A hand-written stand-in would have re-created the original mistake:
it would pass whether or not the gate exists.

``RecordingExchange.create_order`` is the single oracle. ``calls == []`` means no order
reached a venue. The negative control at the end proves the recorder can record, so an
empty list is evidence and not an artefact of a broken fake.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.exchange_executor import CCXTExchangeExecutor  # noqa: E402
from backend_app.backend.execution_environment import ExecutionEnvironment  # noqa: E402
from backend_app.core.execution_engine import ExecutionEngine  # noqa: E402
from backend_app.core.live_execution_gate import (  # noqa: E402
    REQUIRED_CHECKS, LiveExecutionRefused, LiveOrderAuthorization,
    authorize_live_order, require_live_authorization)

pytestmark = pytest.mark.usefixtures("live_platform_enabled")  # RT-15


TENANT = uuid.uuid4()
STRATEGY = "strategy-under-test"
SYMBOL = "BTC/USDT"


def _run(coro):
    return asyncio.run(coro)


# ── the oracle ───────────────────────────────────────────────────────────────


class RecordingExchange:
    """Stands in for the ccxt client. Records every write; performs none."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append((symbol, type_, side, amount, price, params))
        return {
            "id": "venue-order-1",
            "filled": amount,
            "remaining": 0,
            "average": price or 50_000,
            "status": "closed",
        }

    async def cancel_order(self, order_id, symbol=None):
        self.calls.append(("cancel", order_id, symbol))
        return {"id": order_id, "status": "canceled"}


class _Normalizer:
    """The minimum surface `place_order` asks of its normalizer."""

    def format_symbol_for_ccxt(self, symbol):
        return str(symbol)

    def map_order_side(self, side):
        return getattr(side, "value", str(side)).lower()

    def map_order_type(self, order_type):
        return getattr(order_type, "value", str(order_type)).lower()

    def normalize_size(self, _symbol, size):
        return str(size)

    def normalize_price(self, _symbol, price):
        return str(price)

    def market_minimums(self, _symbol):
        # Permissive, so a refusal in these tests is never the venue-minimum check.
        return {"min_amount": Decimal("0"), "min_cost": Decimal("0")}


class _RateLimiter:
    async def acquire(self, _op):
        return None


def build_executor(exchange: RecordingExchange) -> CCXTExchangeExecutor:
    """A REAL CCXTExchangeExecutor whose venue client is the recorder.

    `object.__new__` skips `__init__` so nothing connects, while `place_order` -- the
    method that carries the gate -- is the genuine implementation.
    """
    ex = object.__new__(CCXTExchangeExecutor)
    ex._exchange = exchange
    ex._connected = True
    ex._current_execution_id = None
    ex._current_validation_token = None
    ex._consumed_execution_id = None
    ex._current_live_authorization = None
    ex.exchange_id = "binance"
    ex.sandbox = False
    ex.normalizer = _Normalizer()
    ex.rate_limiter = _RateLimiter()
    ex.api_key = "k"
    ex.api_secret = "s"
    ex.password = None
    return ex


class _Repo:
    """ExecutionRecordRepository stand-in: always grants a fresh claim."""

    def __init__(self, _session=None) -> None:
        pass

    def check_idempotent_execution(self, **kwargs):
        return (f"exec-{uuid.uuid4().hex[:12]}", "proceed", None)

    def claim_execution(self, execution_id, tenant_id):
        return True, object()

    def has_unresolved_submission(self, tenant_id, symbol):
        """RT-17: the live path asks whether the symbol is frozen by an UNKNOWN
        submission. These doubles model a clean account."""
        return False

    def claim_execution_within_capacity(self, execution_id, tenant_id, **kwargs):
        """ADDED BY TRADE-01. The live path now reserves capacity as it claims.

        A double for a safety-critical collaborator has to implement the safety-critical
        method, or the test asserts against a repository that cannot exist. This one
        GRANTS, like `claim_execution` above, so the gates below it are what this file
        measures — in particular the per-order notional ceiling in
        `_execute_trade_internal`, which TRADE-01 adds to rather than replaces.
        """
        return True, object(), None

    def mark_submitted(self, execution_id, tenant_id, **kwargs):
        """ADDED BY TRADE-07, for the same reason `claim_execution_within_capacity` was
        added by TRADE-01.

        The live path now records a submission-evidence timestamp (`submitted_at`) in its
        OWN committed transaction immediately before the venue call, so a crash during
        submission is distinguishable from one before it. `_execute_trade_internal` builds
        a fresh `ExecutionRecordRepository(SessionLocal())` for that write and refuses the
        order if it returns False -- so a double that omits this method makes
        `place_order` unreachable and every assertion in this file about what reaches the
        venue becomes untestable. This double RECORDS successfully, like the other grants
        above, so the venue-boundary gate is what the file measures. The write's own
        failure mode is covered in tests/test_trade07_stale_execution_recovery_postgres.py.
        """
        return True

    def update_status(self, **kwargs):
        return None


class _Session:
    """`execute_with_idempotency` closes its session in a finally; give it something to close."""

    def close(self):
        return None

    def rollback(self):
        return None

    def commit(self):
        return None


class _KillSwitch:
    def __init__(self, active: bool) -> None:
        self._active = active

    async def is_active(self) -> bool:
        return self._active


@pytest.fixture
def harness(monkeypatch):
    """A LIVE engine wired to a recording venue, with every prerequisite satisfied.

    Each test then removes exactly one prerequisite, so a refusal is attributable.
    """
    import backend_app.core.execution_engine as ee

    exchange = RecordingExchange()
    executor = build_executor(exchange)

    monkeypatch.setattr(ee, "ExecutionRecordRepository", _Repo)
    monkeypatch.setattr(ee, "SessionLocal", _Session)
    monkeypatch.setattr(
        ee.execution_metrics, "record_success", lambda **k: None, raising=False
    )

    import backend_app.core.global_safety as gs
    monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _KillSwitch(False))

    engine = ExecutionEngine(
        portfolio_state={"total_equity": 1_000_000.0},
        exchange_executor=executor,
        execution_environment=ExecutionEnvironment.LIVE,
    )
    monkeypatch.setattr(engine, "_validate_strategy_exists", lambda t, s: True)

    return engine, exchange, monkeypatch


def place(engine, **overrides):
    kwargs: Dict[str, Any] = dict(
        tenant_id=TENANT,
        strategy_id=STRATEGY,
        symbol=SYMBOL,
        side="buy",
        size=Decimal("0.01"),
        price=Decimal("50000"),
        source="bot_runner",
        execution_environment=ExecutionEnvironment.LIVE,
        risk_approved=True,
        risk_reason="approved",
    )
    kwargs.update(overrides)
    return _run(engine.execute_with_idempotency(**kwargs))


# ── 1. the positive control. MANDATORY. ─────────────────────────────────────


def test_every_prerequisite_valid_reaches_the_exchange(harness):
    """THE POSITIVE CONTROL.

    Without this, every "no exchange call" assertion below could be passing because the
    harness cannot place an order at all.
    """
    engine, exchange, _ = harness

    result = place(engine)

    assert exchange.calls, (
        f"a fully authorised live order did not reach the venue; result={result}. "
        "Every negative assertion in this file is therefore unproven."
    )
    symbol, _type, side, amount, _price, params = exchange.calls[0]
    assert symbol == SYMBOL
    assert side == "buy"
    assert Decimal(str(amount)) == Decimal("0.01")
    # Venue-side idempotency travels with the order.
    assert params and params.get("clientOrderId")


# ── 2. each prerequisite removed, one at a time ─────────────────────────────


def test_risk_not_evaluated_blocks_the_exchange_write(harness):
    """TRADE-05. A caller that does not evaluate risk cannot execute."""
    engine, exchange, _ = harness

    result = place(engine, risk_approved=None)

    assert exchange.calls == [], "an order with no risk evaluation reached the venue"
    assert result["status"] == "failed"
    assert "risk" in str(result).lower()


def test_risk_explicitly_rejected_blocks_the_exchange_write(harness):
    engine, exchange, _ = harness

    result = place(engine, risk_approved=False, risk_reason="drawdown breached")

    assert exchange.calls == []
    assert result["status"] == "failed"


def test_kill_switch_active_blocks_the_exchange_write(harness):
    engine, exchange, monkeypatch = harness
    import backend_app.core.global_safety as gs
    monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _KillSwitch(True))

    result = place(engine)

    assert exchange.calls == [], "the kill switch was active and an order still went out"
    assert result["status"] == "blocked"


def test_a_non_bot_runner_source_blocks_the_exchange_write(harness):
    engine, exchange, _ = harness

    result = place(engine, source="manual_ui")

    assert exchange.calls == []
    assert result["status"] == "blocked"


def test_an_unauthorized_strategy_blocks_the_exchange_write(harness):
    """Strategy/tenant authorization. Stands in for 'binding absent' at this layer."""
    engine, exchange, monkeypatch = harness
    monkeypatch.setattr(engine, "_validate_strategy_exists", lambda t, s: False)

    result = place(engine)

    assert exchange.calls == []
    assert result["status"] == "blocked"


def test_a_paper_order_on_a_live_engine_blocks_the_exchange_write(harness):
    """Execution-environment validation, through the real guard (it raises)."""
    engine, exchange, _ = harness

    with pytest.raises(Exception):
        place(engine, execution_environment=ExecutionEnvironment.PAPER)

    assert exchange.calls == []


def test_an_unreadable_equity_blocks_the_exchange_write(harness):
    """Capital validation fails closed.

    STATUS CHANGED FROM `failed` TO `blocked` BY TRADE-01, AND THE CONTRACT IS UNCHANGED.
    This test's subject is `exchange.calls == []` — an unreadable equity must not reach
    the venue — and that is asserted identically. What moved is WHERE the refusal happens.
    An unbounded order used to be claimed first and refused later, at the per-order
    ceiling inside `_execute_trade_internal`, reported as `failed`. The capacity
    reservation now needs the same ceiling in order to reserve against it, so an equity it
    cannot read is refused at claim time, before the execution row is moved to
    `EXECUTING`, and reported as `blocked`.

    Refusing before mutating state is strictly better: the old order left a claimed row
    behind for an order that was never fundable. The per-order ceiling is untouched and
    still refuses an oversized single order as `failed` — see the test below, which still
    expects exactly that.
    """
    engine, exchange, _ = harness
    engine.current_equity = Decimal("0")

    result = place(engine)

    assert exchange.calls == []
    assert result["status"] == "blocked"
    assert "capacity" in (result.get("message") or "").lower(), (
        f"the refusal no longer explains itself: {result}"
    )


def test_an_order_above_the_notional_ceiling_blocks_the_exchange_write(harness):
    engine, exchange, _ = harness
    engine.current_equity = Decimal("100")

    result = place(engine, size=Decimal("10"), price=Decimal("50000"))

    assert exchange.calls == []
    assert result["status"] == "failed"


# ── 3. the boundary itself cannot be bypassed ───────────────────────────────


def test_calling_place_order_directly_cannot_reach_the_venue(harness):
    """The venue boundary defends itself, with no gateway involved at all."""
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    with pytest.raises(Exception):
        _run(executor.place_order(
            symbol=SYMBOL, side=OrderSide.buy, order_type=OrderType.market,
            size=Decimal("0.01"),
        ))

    assert exchange.calls == [], (
        "place_order reached the venue without any authorisation. The gate is not on the "
        "boundary."
    )


def test_a_token_without_a_live_authorization_still_cannot_reach_the_venue(harness):
    """The two artefacts are independent, and BOTH are required.

    This is the case the old design missed: holding the anti-bypass token proves the call
    came through the gateway, and says nothing about whether live trading was authorised.
    """
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    exec_id = "exec-forged"
    executor._current_execution_id = exec_id
    executor._current_validation_token = generate_validation_token(
        exec_id, SYMBOL, Decimal("0.01")
    )
    executor._current_live_authorization = None

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(executor.place_order(
            symbol=SYMBOL, side=OrderSide.buy, order_type=OrderType.market,
            size=Decimal("0.01"),
        ))

    assert excinfo.value.code == "LIVE_EXECUTION_NOT_AUTHORIZED"
    assert exchange.calls == []


def test_an_authorization_is_consumed_and_cannot_cover_a_second_order(harness):
    """One authorisation, one order."""
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    def arm(exec_id):
        executor._current_execution_id = exec_id
        executor._current_validation_token = generate_validation_token(
            exec_id, SYMBOL, Decimal("0.01")
        )

    auth = authorize_live_order(
        execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
        symbol=SYMBOL, side="buy", size=Decimal("0.01"), exchange_id="binance",
        checks=REQUIRED_CHECKS,
    )

    arm("exec-1")
    executor._current_live_authorization = auth
    _run(executor.place_order(symbol=SYMBOL, side=OrderSide.buy,
                              order_type=OrderType.market, size=Decimal("0.01")))
    assert len(exchange.calls) == 1

    # Second attempt, same authorisation object, not re-armed.
    arm("exec-1")
    with pytest.raises(LiveExecutionRefused):
        _run(executor.place_order(symbol=SYMBOL, side=OrderSide.buy,
                                  order_type=OrderType.market, size=Decimal("0.01")))
    assert len(exchange.calls) == 1, "a consumed authorisation placed a second order"


def test_an_authorization_for_a_different_order_is_refused(harness):
    """Replaying a 0.01 authorisation against a 10.0 order must fail."""
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    auth = authorize_live_order(
        execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
        symbol=SYMBOL, side="buy", size=Decimal("0.01"), exchange_id="binance",
        checks=REQUIRED_CHECKS,
    )
    executor._current_execution_id = "exec-1"
    executor._current_validation_token = generate_validation_token(
        "exec-1", SYMBOL, Decimal("10")
    )
    executor._current_live_authorization = auth

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(executor.place_order(symbol=SYMBOL, side=OrderSide.buy,
                                  order_type=OrderType.market, size=Decimal("10")))

    assert excinfo.value.code == "LIVE_AUTHORIZATION_ORDER_MISMATCH"
    assert exchange.calls == []


def test_a_forged_signature_is_refused(harness):
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    forged = LiveOrderAuthorization(
        execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
        symbol="BTC-USDT", side="buy", size="0.01", exchange_id="binance",
        checks=frozenset(REQUIRED_CHECKS), signature="deadbeef" * 8,
    )
    executor._current_execution_id = "exec-1"
    executor._current_validation_token = generate_validation_token(
        "exec-1", SYMBOL, Decimal("0.01")
    )
    executor._current_live_authorization = forged

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(executor.place_order(symbol=SYMBOL, side=OrderSide.buy,
                                  order_type=OrderType.market, size=Decimal("0.01")))

    assert excinfo.value.code == "LIVE_AUTHORIZATION_INVALID_SIGNATURE"
    assert exchange.calls == []


@pytest.mark.parametrize("dropped", sorted(REQUIRED_CHECKS))
def test_dropping_any_single_required_check_is_refused(dropped):
    """Each of the required conditions is load-bearing, individually."""
    partial = REQUIRED_CHECKS - {dropped}

    with pytest.raises(LiveExecutionRefused) as excinfo:
        authorize_live_order(
            execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
            symbol=SYMBOL, side="buy", size=Decimal("0.01"), exchange_id="binance",
            checks=partial,
        )

    assert excinfo.value.code == "LIVE_CHECKS_INCOMPLETE"
    assert dropped in excinfo.value.detail


def test_a_partial_authorization_cannot_be_hand_assembled(harness):
    """Even with a VALID signature over a partial set, the boundary refuses.

    The set is re-checked at verification, not only at issue, so an attacker who could
    somehow sign a subset still cannot use it.
    """
    _engine, exchange, _ = harness
    executor = build_executor(exchange)

    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.live_execution_gate import _sign
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    partial = frozenset(REQUIRED_CHECKS - {"risk"})
    auth = LiveOrderAuthorization(
        execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
        symbol="BTC-USDT", side="buy", size="0.01", exchange_id="binance",
        checks=partial,
        signature=_sign(
            execution_id="exec-1", tenant_id=str(TENANT), strategy_id=STRATEGY,
            symbol="BTC-USDT", side="buy", size="0.01", exchange_id="binance",
            checks=partial,
        ),
    )
    executor._current_execution_id = "exec-1"
    executor._current_validation_token = generate_validation_token(
        "exec-1", SYMBOL, Decimal("0.01")
    )
    executor._current_live_authorization = auth

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(executor.place_order(symbol=SYMBOL, side=OrderSide.buy,
                                  order_type=OrderType.market, size=Decimal("0.01")))

    assert excinfo.value.code == "LIVE_CHECKS_INCOMPLETE"
    assert "risk" in excinfo.value.detail
    assert exchange.calls == []


# ── 4. the second door: execute_trade must not confer live permission ───────


def test_execute_trade_cannot_place_a_live_order(harness):
    """TRADE-05, at the door the audit flagged.

    `execute_trade` hardcodes `source="bot_runner"`, so it inherits the gateway's
    authorisation. It does not forward a risk verdict, so it must now be unable to place
    a live order -- which is the whole point of making risk a gateway property.
    """
    engine, exchange, _ = harness

    result = _run(engine.execute_trade(
        tenant_id=TENANT, strategy_id=STRATEGY, symbol=SYMBOL,
        side="buy", size=Decimal("0.01"), price=Decimal("50000"),
    ))

    assert exchange.calls == [], (
        "execute_trade placed a live order without evaluating risk; a future caller "
        "could obtain execution merely by reaching the gateway"
    )
    assert not result.success


# ── 5. negative control: the oracle works ───────────────────────────────────


def test_the_recorder_records(harness):
    """If this fails, every `calls == []` assertion above is meaningless."""
    _engine, exchange, _ = harness

    _run(exchange.create_order(SYMBOL, "market", "buy", 1, None, {}))

    assert len(exchange.calls) == 1


def test_the_gate_helper_admits_a_complete_set():
    """The gate is not simply refusing everything."""
    auth = authorize_live_order(
        execution_id="e", tenant_id="t", strategy_id="s", symbol=SYMBOL,
        side="buy", size=Decimal("0.01"), exchange_id="binance",
        checks=REQUIRED_CHECKS,
    )
    assert auth.missing_checks() == frozenset()
    assert require_live_authorization(
        auth, symbol=SYMBOL, side="buy", size=Decimal("0.01"), exchange_id="binance"
    ) is auth
