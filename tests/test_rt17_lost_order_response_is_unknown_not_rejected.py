"""RT-17 -- a lost order response is UNKNOWN, never a rejection.

THE DEFECT
    ``CCXTExchangeExecutor.place_order`` turned *every* exception from ``create_order``
    into ``OrderResult(status="rejected")`` and the engine persisted it as FAILED. A
    timeout / dropped connection / response without an order id says nothing about whether
    the venue accepted the order. Recording it as a rejection lets the strategy roll back
    and re-enter on the next tick (new price => new execution id) and double the position.

THE FIX (this file proves it)
    1. executor: failures at/after the venue call that are ccxt ``OperationFailed`` (or any
       non-ccxt error) or a response with no order id => ``status="unknown"``,
       ``error_code="SUBMISSION_UNKNOWN"``. ccxt ``ExchangeError`` (the venue answered and
       refused) and every pre-submit failure stay ``rejected``.
    2. engine: an unknown outcome is persisted as ``ExecutionStatus.UNKNOWN`` (not FAILED)
       and reported as ``submission_unknown``.
    3. symbol freeze: while a tenant holds an UNKNOWN row for a symbol, a NEW order for that
       symbol is refused before any venue call.

SCOPE. Offline: recording doubles, no network, no credential, no real order. What this does
NOT show: how a given venue actually behaves on timeout, or adjudication of the UNKNOWN row
(OrderWatchdog has no production executor -- RT-16).
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import ccxt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend_app.core.execution_engine as ee
from backend_app.backend.exchange_executor import _outcome_is_ambiguous
from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionRecordRepository,
                                                      ExecutionStatus)
from tests.test_trade04_live_order_gate_at_venue_boundary import (
    SYMBOL, TENANT, RecordingExchange, _KillSwitch, _Session, build_executor, place)

pytestmark = pytest.mark.usefixtures("live_platform_enabled")


# ── 1. classification ────────────────────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    ccxt.RequestTimeout("t"), ccxt.NetworkError("n"), ccxt.ExchangeNotAvailable("x"),
    ccxt.DDoSProtection("d"), ccxt.RateLimitExceeded("r"), ccxt.BadResponse("b"),
    asyncio.TimeoutError(), ConnectionResetError("reset"), RuntimeError("???"),
])
def test_lost_or_unclassified_outcomes_are_ambiguous(exc):
    assert _outcome_is_ambiguous(exc) is True


@pytest.mark.parametrize("exc", [
    ccxt.InsufficientFunds("f"), ccxt.InvalidOrder("i"), ccxt.BadSymbol("s"),
    ccxt.AuthenticationError("a"), ccxt.PermissionDenied("p"), ccxt.OrderNotFound("o"),
])
def test_venue_refusals_are_definite_rejections(exc):
    assert _outcome_is_ambiguous(exc) is False


# ── 2. the executor, real place_order, scripted venue ────────────────────────


class ScriptedExchange(RecordingExchange):
    def __init__(self, outcome):
        super().__init__()
        self._outcome = outcome

    async def create_order(self, *a, **k):
        self.calls.append(a)
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _live_executor_place(exchange):
    """Drive the genuine executor.place_order through the genuine engine path."""
    import backend_app.core.global_safety as gs
    return build_executor(exchange), gs


@pytest.fixture
def pg_like(monkeypatch):
    """Engine + executor over a real in-memory SQLite execution_records table."""
    db = create_engine("sqlite://")
    ExecutionRecordModel.__table__.create(db)
    Session = sessionmaker(bind=db)

    monkeypatch.setattr(ee, "SessionLocal", Session)
    monkeypatch.setattr(ee.execution_metrics, "record_success", lambda **k: None, raising=False)

    import backend_app.core.global_safety as gs
    monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _KillSwitch(False))

    # SQLite cannot take the advisory-lock capacity claim; grant the claim, keep real rows.
    real_claim = ExecutionRecordRepository.claim_execution

    def _claim_within(self, execution_id, tenant_id, **kw):
        ok, rec = real_claim(self, execution_id, tenant_id)
        return ok, rec, None

    monkeypatch.setattr(ExecutionRecordRepository, "claim_execution_within_capacity", _claim_within)

    def make(exchange):
        executor = build_executor(exchange)
        # build_executor skips __init__; the error-redaction path reads these.
        executor._api_key, executor._api_secret, executor._password = "k", "s", None
        engine = ExecutionEngine(
            portfolio_state={"total_equity": 1_000_000.0},
            exchange_executor=executor,
            execution_environment=ExecutionEnvironment.LIVE,
        )
        monkeypatch.setattr(engine, "_validate_strategy_exists", lambda t, s: True)
        return engine

    return make, Session


def _rows(Session):
    s = Session()
    try:
        return s.query(ExecutionRecordModel).all()
    finally:
        s.close()


@pytest.mark.parametrize("exc", [
    ccxt.RequestTimeout("timed out"), ccxt.NetworkError("reset"), ccxt.BadResponse("garbled"),
])
def test_timeout_after_send_is_unknown_and_persisted_as_unknown(pg_like, exc):
    make, Session = pg_like
    exchange = ScriptedExchange(exc)
    engine = make(exchange)

    result = place(engine)

    assert exchange.calls, "positive control: the order was sent"
    assert result["status"] == "submission_unknown", result
    rows = _rows(Session)
    assert len(rows) == 1
    assert rows[0].status == ExecutionStatus.UNKNOWN, "must NOT be FAILED"
    assert rows[0].submitted_at is not None


def test_response_without_order_id_is_unknown(pg_like):
    make, Session = pg_like
    engine = make(ScriptedExchange({"status": "open"}))   # no "id"

    result = place(engine)

    assert result["status"] == "submission_unknown", result
    assert _rows(Session)[0].status == ExecutionStatus.UNKNOWN


def test_venue_refusal_is_still_a_plain_failure(pg_like):
    make, Session = pg_like
    engine = make(ScriptedExchange(ccxt.InsufficientFunds("no funds")))

    result = place(engine)

    assert result["status"] == "failed", result
    assert _rows(Session)[0].status == ExecutionStatus.FAILED


def test_success_is_unchanged(pg_like):
    make, Session = pg_like
    engine = make(RecordingExchange())

    assert place(engine)["status"] == "completed"
    assert _rows(Session)[0].status == ExecutionStatus.COMPLETED


# ── 3. the symbol freeze ─────────────────────────────────────────────────────


def test_unknown_submission_freezes_new_orders_for_that_symbol(pg_like):
    make, _ = pg_like
    timeout_exchange = ScriptedExchange(ccxt.RequestTimeout("timed out"))
    engine = make(timeout_exchange)
    assert place(engine)["status"] == "submission_unknown"
    sent_before = len(timeout_exchange.calls)

    # A different price => a different execution id, so only the freeze can stop it.
    second = place(engine, price=Decimal("50123"))

    assert second["status"] == "blocked", second
    assert "frozen" in second["message"]
    assert len(timeout_exchange.calls) == sent_before, "no second order may reach the venue"


def test_freeze_is_per_symbol_and_per_tenant(pg_like):
    make, _ = pg_like
    ex = ScriptedExchange(ccxt.RequestTimeout("timed out"))
    engine = make(ex)
    assert place(engine)["status"] == "submission_unknown"

    ex._outcome = {"id": "ok-1", "filled": 0.01, "remaining": 0, "average": 1, "status": "closed"}
    other_symbol = place(engine, symbol="ETH/USDT")
    other_tenant = place(engine, tenant_id=uuid.uuid4())

    assert other_symbol["status"] == "completed", other_symbol
    assert other_tenant["status"] == "completed", other_tenant


def test_freeze_lifts_when_the_unknown_row_is_adjudicated(pg_like):
    make, Session = pg_like
    ex = ScriptedExchange(ccxt.RequestTimeout("timed out"))
    engine = make(ex)
    assert place(engine)["status"] == "submission_unknown"
    assert place(engine, price=Decimal("50123"))["status"] == "blocked"

    s = Session()
    row = s.query(ExecutionRecordModel).filter_by(status=ExecutionStatus.UNKNOWN).one()
    row.status = ExecutionStatus.FAILED     # adjudicated: venue confirmed nothing exists
    s.commit()
    s.close()
    ex._outcome = {"id": "ok-2", "filled": 0.01, "remaining": 0, "average": 1, "status": "closed"}

    assert place(engine, price=Decimal("50321"))["status"] == "completed"


def test_freeze_check_failure_fails_closed(pg_like, monkeypatch):
    make, _ = pg_like
    ex = RecordingExchange()
    engine = make(ex)

    def boom(self, tenant_id, symbol):
        raise RuntimeError("db down")

    monkeypatch.setattr(ExecutionRecordRepository, "has_unresolved_submission", boom)

    assert place(engine)["status"] == "blocked"
    assert not ex.calls


def test_repository_symbol_comparison_is_normalised():
    db = create_engine("sqlite://")
    ExecutionRecordModel.__table__.create(db)
    s = sessionmaker(bind=db)()
    repo = ExecutionRecordRepository(s)
    t = uuid.uuid4()
    s.add(ExecutionRecordModel(
        execution_id="e1", tenant_id=t, strategy_id="s", symbol="BTC-USDT", side="buy",
        size="1", status=ExecutionStatus.UNKNOWN))
    s.commit()
    assert repo.has_unresolved_submission(t, "BTC/USDT") is True
    assert repo.has_unresolved_submission(t, "btcusdt") is True
    assert repo.has_unresolved_submission(t, "ETH/USDT") is False
    assert repo.has_unresolved_submission(uuid.uuid4(), "BTC/USDT") is False
    s.close()
