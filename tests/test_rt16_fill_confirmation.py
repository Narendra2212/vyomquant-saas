"""RT-16 -- "accepted by the venue" is not "filled".

THE DEFECT (audit)
    `ExecutionEngine.execute_with_idempotency` wrote `ExecutionStatus.COMPLETED` the
    instant `exchange_executor.place_order(...)` returned success, for every order type.
    `place_order` itself hardcodes `status="pending"` by design ("STEP 6.4: Returns
    'pending' - wait for event system for FILLED") -- so a LIMIT order that the venue
    merely ACCEPTED (not filled) was recorded COMPLETED anyway. Because
    `OrderWatchdog._find_open_orders` only selects `PENDING`/`EXECUTING` rows, that limit
    order became permanently invisible to the one mechanism that could ever notice it was
    never actually filled.

THE FIX (see execution_engine.py, both call sites tagged RT-16)
    `_execute_trade_internal`'s live branch now derives `fully_filled` from the ONLY
    honest signal `place_order` returns: the venue's own `filled`/`remaining` amounts
    (never from `result.status`, which is always `"pending"`). `execute_with_idempotency`
    writes COMPLETED only when `fully_filled` is true; otherwise the row is left
    EXECUTING (the state `claim_execution_within_capacity` already put it in before the
    venue call) for `OrderWatchdog`'s reconciliation loop to resolve once it observes a
    real fill.

    `OrderWatchdog._update_position`'s swallowed exception (around the
    `PositionEngine.update_position_from_fill` call) now also raises a critical alert
    through the SAME `AlertSystem.send_critical_alert` mechanism `_trigger_alert` already
    uses for a stuck order, instead of only logging at `error`. The reconciliation loop
    still continues for every other order either way.

WHAT THIS FILE PROVES
    (a) placing a limit order that the venue only accepted does NOT immediately mark it
        COMPLETED (driven through the real `execute_with_idempotency` ->
        `_execute_trade_internal` -> `CCXTExchangeExecutor.place_order` path, exactly like
        tests/test_trade04_live_order_gate_at_venue_boundary.py);
    (d) a market order the venue reports as fully filled immediately still IS marked
        COMPLETED -- the regression guard that this change did not touch the correct path;
    (b) `OrderWatchdog`'s reconciliation loop, observing a real fill from the (fake)
        exchange, transitions an EXECUTING order to COMPLETED and calls
        `PositionEngine.update_position_from_fill`, which creates a real row in a REAL
        `positions` table (SQLite, both tables from the same declarative `Base`);
    (c) if that position-update call raises, the new alert fires (a spy on
        `AlertSystem.send_critical_alert`) and the reconciliation loop still completes for
        a second, unrelated order in the same pass.

NOT SHOWN: real sandbox/exchange certification (no exchange sandbox credentials in this
environment) -- the fake venue clients here model CCXT's response shape (`filled`,
`remaining`, `id`) but are not the real exchange.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.exchange_executor import CCXTExchangeExecutor  # noqa: E402
from backend_app.backend.execution_environment import ExecutionEnvironment  # noqa: E402
from backend_app.core.execution_engine import ExecutionEngine  # noqa: E402
from backend_app.core.models.execution_record import ExecutionStatus  # noqa: E402

pytestmark = pytest.mark.usefixtures("live_platform_enabled")  # RT-15

TENANT = uuid.uuid4()
STRATEGY = "strategy-under-test"
SYMBOL = "BTC/USDT"


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# (a) / (d): driven through the real execute_with_idempotency -> place_order
#     path, same harness shape as test_trade04_live_order_gate_at_venue_boundary.py
# ═══════════════════════════════════════════════════════════════════════════


class RecordingExchange:
    """Stands in for the ccxt client. The `filled`/`remaining` values are the whole
    point of this file: they are the ONLY signal `place_order` passes through that
    distinguishes an accepted-but-resting order from a genuinely filled one."""

    def __init__(self, *, filled: float, remaining: float) -> None:
        self.calls: List[tuple] = []
        self._filled = filled
        self._remaining = remaining

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append((symbol, type_, side, amount, price, params))
        return {
            "id": "venue-order-1",
            "filled": self._filled,
            "remaining": self._remaining,
            "average": price or 50_000,
            "status": "closed" if self._remaining == 0 else "open",
        }


class _Normalizer:
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
        return {"min_amount": Decimal("0"), "min_cost": Decimal("0")}


class _RateLimiter:
    async def acquire(self, _op):
        return None


def build_executor(exchange: RecordingExchange) -> CCXTExchangeExecutor:
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
    """ExecutionRecordRepository stand-in that RECORDS what status it was asked to
    write, so these tests can assert on it directly rather than only on the returned
    dict."""

    calls: List[Dict[str, Any]] = []

    def __init__(self, _session=None) -> None:
        pass

    def check_idempotent_execution(self, **kwargs):
        return (f"exec-{uuid.uuid4().hex[:12]}", "proceed", None)

    def claim_execution(self, execution_id, tenant_id):
        return True, object()

    def has_unresolved_submission(self, tenant_id, symbol):
        return False

    def claim_execution_within_capacity(self, execution_id, tenant_id, **kwargs):
        return True, object(), None

    def mark_submitted(self, execution_id, tenant_id, **kwargs):
        return True

    def update_status(self, **kwargs):
        type(self).calls.append(kwargs)
        return None


class _Session:
    def close(self):
        return None

    def rollback(self):
        return None

    def commit(self):
        return None


@pytest.fixture
def harness(monkeypatch):
    import backend_app.core.execution_engine as ee

    _Repo.calls = []
    monkeypatch.setattr(ee, "ExecutionRecordRepository", _Repo)
    monkeypatch.setattr(ee, "SessionLocal", _Session)
    monkeypatch.setattr(
        ee.execution_metrics, "record_success", lambda **k: None, raising=False
    )

    import backend_app.core.global_safety as gs

    class _KillSwitch:
        async def is_active(self):
            return False

    monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _KillSwitch())

    def _build(exchange: RecordingExchange) -> ExecutionEngine:
        executor = build_executor(exchange)
        engine = ExecutionEngine(
            portfolio_state={"total_equity": 1_000_000.0},
            exchange_executor=executor,
            execution_environment=ExecutionEnvironment.LIVE,
        )
        monkeypatch.setattr(engine, "_validate_strategy_exists", lambda t, s: True)
        return engine

    return _build


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


def test_a_limit_order_merely_accepted_is_not_marked_completed(harness):
    """(a). The venue accepted the order (got an id) but reports filled=0,
    remaining=the whole size -- exactly CCXT's shape for a resting limit order."""
    exchange = RecordingExchange(filled=0, remaining=0.01)
    engine = harness(exchange)

    result = place(engine, price=Decimal("49000"))  # a limit price

    assert exchange.calls, "the order never reached the venue; nothing is proven"
    assert result["status"] != "completed", (
        f"a merely-accepted limit order was recorded as completed: {result}"
    )
    assert _Repo.calls, "update_status was never called"
    written_status = _Repo.calls[-1]["status"]
    assert written_status == ExecutionStatus.EXECUTING, (
        f"expected the row left EXECUTING for OrderWatchdog to resolve, got "
        f"{written_status!r}"
    )
    # EXECUTING is one of the two statuses OrderWatchdog._find_open_orders selects.
    from backend_app.backend.order_watchdog import OrderWatchdog

    import inspect
    source = inspect.getsource(OrderWatchdog._find_open_orders)
    assert "ExecutionStatus.EXECUTING" in source


def test_a_partially_filled_limit_order_is_not_marked_completed(harness):
    """Same defect, partial-fill shape: filled > 0 but remaining > 0 is still not a
    confirmed COMPLETE fill."""
    exchange = RecordingExchange(filled=0.004, remaining=0.006)
    engine = harness(exchange)

    result = place(engine, size=Decimal("0.01"), price=Decimal("49000"))

    assert result["status"] != "completed"
    assert _Repo.calls[-1]["status"] == ExecutionStatus.EXECUTING


def test_an_ambiguous_fill_response_is_treated_conservatively(harness):
    """No `remaining` at all in the venue response (the executor passes None through
    in that case) -- the conservative default this finding calls for is "not
    confirmed", not "assume filled"."""

    class _NoRemaining(RecordingExchange):
        async def create_order(self, symbol, type_, side, amount, price=None, params=None):
            self.calls.append((symbol, type_, side, amount, price, params))
            return {"id": "venue-order-amb", "filled": 0, "average": price, "status": "open"}
            # Deliberately no "remaining" key.

    exchange = _NoRemaining(filled=0, remaining=0)
    engine = harness(exchange)

    result = place(engine, price=Decimal("49000"))

    assert result["status"] != "completed"
    assert _Repo.calls[-1]["status"] == ExecutionStatus.EXECUTING


def test_an_order_genuinely_filled_immediately_is_still_marked_completed(harness):
    """(d). REGRESSION GUARD. An order the venue reports as fully filled immediately
    (filled == amount, remaining == 0 -- the ordinary CCXT shape for a market order, and
    for a limit order aggressive enough to cross the book on arrival) must still be
    recorded COMPLETED, exactly as before this change.

    NOTE ON ORDER TYPE: `_execute_trade_internal` derives `market` vs `limit` purely
    from `price <= 0` (a market order carries no price), and a market order's price-less
    notional is independently refused by the TRADE-11 capacity gate a few lines above the
    `place_order` call (`"a market order arrives here as price <= 0 ... refused rather
    than valued by a guess"`) -- a separate, pre-existing constraint on this path, not
    something RT-16 touches. Every real caller in this codebase
    (`backend/master_executor.py`) therefore submits a priced order, which is why this
    regression guard does too; what RT-16 changed is orthogonal to order TYPE and keyed
    only on the venue's `filled`/`remaining` evidence.
    """
    exchange = RecordingExchange(filled=0.01, remaining=0)
    engine = harness(exchange)

    result = place(engine, price=Decimal("50000"))

    assert exchange.calls
    assert result["status"] == "completed", (
        f"a genuinely, immediately filled order regressed: {result}"
    )
    assert _Repo.calls[-1]["status"] == ExecutionStatus.COMPLETED


def test_the_paper_ledger_path_is_unaffected(harness):
    """The paper paths never set `fully_filled` (there is no venue to wait on); the
    default of True must keep their COMPLETED behaviour exactly as it was."""
    from backend_app.core.execution_engine import ExecutionEngine as EE

    engine = EE(
        portfolio_state={"total_equity": 100_000.0},
        exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
    )

    success, trade_result = _run(
        engine._execute_trade_internal(
            symbol="BTCUSDT", side="buy", size=Decimal("0.01"), price=Decimal("50000"),
            execution_id="exec_paper_rt16",
        )
    )

    assert success is True
    assert "fully_filled" not in trade_result



# ═══════════════════════════════════════════════════════════════════════════
# (b) / (c): the watchdog's reconciliation loop, against a REAL `positions` table
#
# WHY REAL POSTGRES, NOT SQLITE. `_update_order_from_status`'s position-lock
# statement (`order_watchdog._position_lock_sql`) ends in `FOR UPDATE`, which
# SQLite cannot parse -- that function's own docstring says so explicitly
# ("the real lock is exercised against PostgreSQL in the database-tests job").
# On SQLite the lock attempt raises, the handler's recovery calls
# `self.db.rollback()`, and that rollback discards the in-memory, not-yet-committed
# `order.status = COMPLETED` assignment the same method made a few lines above --
# a pre-existing SQLite-only incompatibility in this method, independent of RT-16
# and out of this finding's scope. Gated on `AERORA_TEST_DATABASE_URL`, following
# tests/test_trade07_stale_execution_recovery_postgres.py and
# tests/test_position_lifecycle_postgres.py's own, identical convention for this
# exact reason.
# ═══════════════════════════════════════════════════════════════════════════

import os  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig  # noqa: E402
from backend_app.core.models.execution_record import ExecutionRecordModel  # noqa: E402
from backend_app.core.position_model import PositionModel  # noqa: E402

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
        "here is NOT a pass -- the watchdog-to-PositionEngine fill-confirmation chain "
        "is unverified without it."
    ),
)

WD_SYMBOL = "ETH/USDT"


def _pg_engine():
    return create_engine(_PG_URL, future=True, poolclass=NullPool)


@pytest.fixture
def wd_db():
    engine = _pg_engine()
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def wd_tenant(wd_db):
    """A tenant satisfying `execution_records`'/`positions`'/`orders`' FK chain --
    same pattern as test_trade07_stale_execution_recovery_postgres.py's `tenant`
    fixture."""
    tenant_id = str(uuid.uuid4())
    wd_db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"rt16-fill-{tenant_id[:8]}@example.invalid"},
    )
    wd_db.execute(
        text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"rt16-fill-{tenant_id[:8]}@example.invalid"},
    )
    wd_db.commit()

    yield tenant_id

    wd_db.rollback()
    wd_db.execute(
        text("DELETE FROM public.positions WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    wd_db.execute(
        text("DELETE FROM public.execution_records WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    wd_db.execute(
        text("DELETE FROM public.orders WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    wd_db.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
                  {"t": tenant_id})
    wd_db.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"),
                  {"t": tenant_id})
    wd_db.commit()


def _seed_order(session, tenant_id, *, execution_id, filled_size="0",
                 remaining_size="0.01", symbol=WD_SYMBOL, size="0.01"):
    """An EXECUTING row with a REAL `orders` row backing `order_id` (TRADE-12:
    `order_id` carries `FOREIGN KEY (order_id) REFERENCES orders(id)`, so an
    arbitrary string is refused -- same discovery as
    test_trade07_stale_execution_recovery_postgres.py's own comment on this)."""
    platform_order_id = f"ord_rt16_{execution_id}"
    session.execute(
        text("INSERT INTO public.orders (id, tenant_id, created_at, updated_at) "
             "VALUES (:o, CAST(:t AS uuid), now(), now())"),
        {"o": platform_order_id, "t": tenant_id},
    )
    session.execute(
        text(
            "INSERT INTO public.execution_records "
            "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
            " order_id, filled_size, remaining_size, submitted_at, created_at, updated_at) "
            "VALUES (:e, CAST(:t AS uuid), 'strat-rt16-fill', :sym, 'buy', :size, '2000', "
            "        CAST('EXECUTING' AS executionstatus), :oid, :filled, :remaining, "
            "        now(), now(), now())"
        ),
        {
            "e": execution_id, "t": tenant_id, "sym": symbol, "size": size,
            "oid": platform_order_id, "filled": filled_size, "remaining": remaining_size,
        },
    )
    session.commit()
    return platform_order_id


def _status_of(session, execution_id) -> str:
    row = session.execute(
        text("SELECT status::text FROM public.execution_records WHERE execution_id = :e"),
        {"e": execution_id},
    ).fetchone()
    assert row is not None
    return row[0]


def _positions_for(session, tenant_id, symbol=None):
    q = "SELECT symbol, size FROM public.positions WHERE tenant_id = CAST(:t AS uuid)"
    params = {"t": tenant_id}
    if symbol is not None:
        q += " AND symbol = :sym"
        params["sym"] = symbol
    return session.execute(text(q), params).fetchall()


class _FakeExecutor:
    """`OrderWatchdog._get_executor_for_order`'s real replacement: reports the venue's
    CURRENT, authoritative state for a resting order."""

    def __init__(self, *, status: str, filled_size: str, remaining_size: str = "0",
                 avg_price: str = "2000"):
        self._status, self._filled_size = status, filled_size
        self._remaining_size, self._avg_price = remaining_size, avg_price

    async def get_order_status(self, exchange_order_id, symbol):
        return SimpleNamespace(
            success=True, status=self._status, filled_size=self._filled_size,
            remaining_size=self._remaining_size, avg_price=self._avg_price,
            error_message=None,
        )


def _watchdog(session, executor_for_order) -> OrderWatchdog:
    w = OrderWatchdog(session, WatchdogConfig())
    if isinstance(executor_for_order, dict):
        async def _pick(order):
            return executor_for_order.get(order.execution_id)
        w._get_executor_for_order = _pick
    else:
        w._get_executor_for_order = AsyncMock(return_value=executor_for_order)
    w._trigger_alert = AsyncMock()
    return w


@pg_only
@pytest.mark.asyncio
async def test_watchdog_confirms_a_real_fill_and_updates_a_real_position(
    wd_db, wd_tenant
):
    """(b). An EXECUTING order (RT-16's new, correct resting state) is observed by the
    watchdog to have genuinely filled at the venue: the row becomes COMPLETED AND a real
    `positions` row is created with the fill applied."""
    _seed_order(wd_db, wd_tenant, execution_id="exec_rt16fill_b", filled_size="0")
    executor = _FakeExecutor(status="filled", filled_size="0.01", remaining_size="0")
    w = _watchdog(wd_db, executor)

    await w._reconcile_open_orders()

    assert _status_of(wd_db, "exec_rt16fill_b") == "COMPLETED", (
        "watchdog did not confirm the fill and complete the order"
    )
    positions = _positions_for(wd_db, wd_tenant)
    assert len(positions) == 1, (
        f"PositionEngine.update_position_from_fill did not create a position row; "
        f"rows={positions}"
    )
    assert positions[0].symbol == WD_SYMBOL
    assert Decimal(positions[0].size) == Decimal("0.01")


@pg_only
@pytest.mark.asyncio
async def test_watchdog_leaves_an_unfilled_order_alone(wd_db, wd_tenant):
    """Negative control for (b): if the venue still reports the order resting
    (unchanged), nothing is completed and no position is created."""
    _seed_order(wd_db, wd_tenant, execution_id="exec_rt16fill_neg", filled_size="0")
    executor = _FakeExecutor(status="open", filled_size="0", remaining_size="0.01")
    w = _watchdog(wd_db, executor)

    await w._reconcile_open_orders()

    assert _status_of(wd_db, "exec_rt16fill_neg") == "EXECUTING"
    assert _positions_for(wd_db, wd_tenant) == []


@pg_only
@pytest.mark.asyncio
async def test_a_position_update_failure_alerts_and_the_loop_still_completes(
    wd_db, wd_tenant, monkeypatch
):
    """(c). `PositionEngine.update_position_from_fill` raises for one order; the new
    alert fires for it, AND a second, unrelated order in the same reconciliation pass
    still completes normally (one order's failure must not break the loop for others)."""
    _seed_order(
        wd_db, wd_tenant, execution_id="exec_rt16fill_c_boom", filled_size="0",
        symbol=WD_SYMBOL,
    )
    _seed_order(
        wd_db, wd_tenant, execution_id="exec_rt16fill_c_ok", filled_size="0",
        symbol="LTC/USDT",
    )

    executors = {
        "exec_rt16fill_c_boom": _FakeExecutor(
            status="filled", filled_size="0.01", remaining_size="0"
        ),
        "exec_rt16fill_c_ok": _FakeExecutor(
            status="filled", filled_size="0.01", remaining_size="0"
        ),
    }
    w = _watchdog(wd_db, executors)

    import backend_app.backend.position_engine as pe_mod

    real_engine_cls = pe_mod.PositionEngine
    original_update = real_engine_cls.update_position_from_fill

    async def _maybe_boom(self, execution_record, fill_size=None, fill_price=None):
        if execution_record.symbol == WD_SYMBOL:
            raise RuntimeError("simulated PositionEngine failure")
        return await original_update(
            self, execution_record, fill_size=fill_size, fill_price=fill_price
        )

    monkeypatch.setattr(real_engine_cls, "update_position_from_fill", _maybe_boom)

    sent_alerts = []

    class _FakeAlertSystem:
        async def send_critical_alert(self, **kwargs):
            sent_alerts.append(kwargs)
            return True

    import backend_app.backend.alert_system as alert_mod
    monkeypatch.setattr(alert_mod, "get_alert_system", lambda: _FakeAlertSystem())

    await w._reconcile_open_orders()

    assert sent_alerts, (
        "PositionEngine.update_position_from_fill raised but no alert was sent"
    )
    assert any(
        "exec_rt16fill_c_boom" in str(a.get("metadata", {}).get("execution_id", ""))
        or "exec_rt16fill_c_boom" in str(a.get("message", ""))
        for a in sent_alerts
    ), f"the alert did not identify the failing order: {sent_alerts}"

    # The order whose position-update raised: the order row itself is still marked
    # filled by `_update_order_from_status` (that assignment and commit happen before
    # the position call and are unaffected by its failure) -- but no position was
    # created for it, and that absence is what the alert exists to surface.
    assert _status_of(wd_db, "exec_rt16fill_c_boom") == "COMPLETED"
    assert _positions_for(wd_db, wd_tenant, symbol=WD_SYMBOL) == []

    # The second, unrelated order in the same pass is unaffected: completed, with its
    # own real position row. This is the "loop still completes" half of (c).
    assert _status_of(wd_db, "exec_rt16fill_c_ok") == "COMPLETED"
    assert len(_positions_for(wd_db, wd_tenant, symbol="LTC/USDT")) == 1
