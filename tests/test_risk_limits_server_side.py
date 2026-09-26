"""
tests/test_risk_limits_server_side.py - task 12.3, Requirements 1.13, 2.13.

Spec: production-launch-hardening. ``design.md`` -> "1.13 Server-side risk enforcement".
"Submit each violation - position size, leverage, daily loss, kill switch - with no UI
involvement, asserting refusal with a distinct machine-readable code and that no order row is
written. Not via ``POST /api/orders/execute`` or ``POST /api/orders/create`` - those answer 403
``MANUAL_EXECUTION_BLOCKED`` to everything by design, so a pass there proves nothing (see
``backend_app/routers/orders.py`` lines ~559-729, untouched here and not imported by this file).
Target the paper order route and the internal execution service."

THE TWO MECHANISMS THIS FILE TARGETS, AND WHY THESE TWO
---------------------------------------------------------
1. **The paper order route.** ``POST /api/paper/orders`` (``routers/paper_trading.py``,
   ``place_paper_order``) delegates to ``PaperTradingService.place_order``
   (``backend_app/backend/paper_trading_service.py:1152``), which reads
   ``routers.risk.get_user_risk_settings_store`` / ``is_user_kill_switched`` /
   ``_user_strategy_limits`` and raises a bare ``ValueError`` before any order row exists for
   four of the checks. Driven directly against the service, no HTTP client, per this
   repository's established convention (``bind_paper_persistence`` /
   ``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this tree
   uses, the same one ``tests/test_paper_order_lifecycle_writes.py`` and
   ``tests/test_risk_gate_enforcement.py`` already drive).
2. **The internal execution service.** ``core.execution_engine.ExecutionEngine.open_position``
   (``backend_app/core/execution_engine.py:611``), whose docstring names exactly this task's
   guardrails ("Max position size: 10% of equity; Max daily loss: 5% of equity; Max open
   trades: 5 positions") and which calls ``self.risk_manager.can_open_position`` (an
   ``InstitutionalRiskManager`` = ``RiskManager``, ``backend_app/core/risk_manager.py:437``)
   BEFORE any position is recorded. ``ExecutionEngine`` is a plain, synchronous, DB-free
   Python object (``ExecutionEngine(fee_rate=..., portfolio_state={...})`` -
   ``backend_app/validate_financials.py`` constructs it exactly this way) and is reachable
   without a running worker, unlike ``execute_with_idempotency`` / ``execute_trade``'s live
   path (which requires ``source="bot_runner"``, a live-only ``execution_environment`` and a
   Postgres-backed ``ExecutionRecordRepository``) and unlike ``strategies.py`` /
   ``core/live_engine.py``'s callers of ``open_position``, which only reach it from inside a
   backtest loop or a running ``LiveTradingEngine`` process respectively.

   ``backend/dag_risk_integration.RiskIntegratedExecutionPipeline.check_risk`` was considered as
   a second execution-service candidate - it names ``BLOCKED_POSITION_SIZE``,
   ``BLOCKED_DRAWDOWN``, ``BLOCKED_DAILY_LIMIT`` and ``BLOCKED_KILL_SWITCH`` as an ``Enum``,
   which is the closest thing to a real machine-readable risk code anywhere in this codebase.
   It is NOT included here: ``backend/signal_service.py`` (lines ~2921-2932, ~3952-3956) names
   it as "the real risk validator on this path" for the path that only exists once
   ``RiskIntegratedEventLoop`` is constructed by ``POST /api/strategies/risk/start``, which
   wires a live or simulated market-data event source and starts an event loop - i.e. it
   belongs to the deployment path this task records as BLOCKED, not to a mechanism provable
   without a worker. Exercising ``check_risk`` in isolation, detached from the event loop that
   is its only production caller, would prove a fact about a class and not about a reachable
   path, which is the same reasoning ``design.md`` applies elsewhere in this pass to distinguish
   a provable mechanism from an unreachable one.

WHAT ALREADY EXISTS, AND WHAT THIS FILE ADDS (NOT A DUPLICATE SWEEP)
---------------------------------------------------------------------
``tests/test_risk_gate_enforcement.py`` and ``tests/test_risk_management_lifecycle.py`` already
exercise kill switch, daily loss, max positions and strategy position size against
``PaperTradingService.place_order`` through the same ``FakeSupabase`` double, and assert the
``ValueError`` message and that the account balance / position count is unchanged. 1.13's
"no test in this tree establishes" is, for those four checks on the paper route, **not quite
right** - task 12.1 records the identical situation for 1.11 and this file records it the same
way rather than re-asserting it silently.

What those two files do not do, and what this file adds:

* They assert on the ``ValueError``'s message text (``pytest.raises(match=...)``). Requirement
  2.13 asks for "a distinct machine-readable code", and the message is not one - it is prose.
  The machine-readable code these checks actually carry is the ``rule`` argument
  ``paper_trading_service.place_order`` passes into
  ``routers.risk.record_risk_violation(uid, rule, reason)`` beside the raise
  (``KILL_SWITCH_ACTIVE``, ``MAX_DAILY_LOSS_EXCEEDED``, ``MAX_POSITIONS_EXCEEDED``,
  ``MAX_POSITION_SIZE_EXCEEDED``). This file captures and asserts THAT code, at its source,
  rather than pattern-matching a sentence meant for a human.
* They assert "the account balance and position count are unchanged". This file asserts the
  stricter claim Requirement 1.13 actually asks for: that **no statement was issued against any
  ``paper_*`` write table at all** (``FakeSupabase.statements``, the same ledger
  ``tests/test_paper_order_lifecycle_writes.py``'s ``_wrote_since`` reads), which is a claim
  about every write table and not only about the two the existing assertions happened to check.
* Neither file touches the execution service (``ExecutionEngine.open_position`` /
  ``RiskManager.can_open_position``) at all. This file adds that half.
* Neither file exercises leverage, because - see below - there is nothing on either reachable
  path to exercise.

THE FOUR VIOLATION TYPES, MECHANISM BY MECHANISM
---------------------------------------------------
============  =========================================  =========================================
Violation      Paper route                                 Execution service
============  =========================================  =========================================
Kill switch    Enforced. ``is_user_kill_switched`` ->      Not applicable to a bare ``ExecutionEngine``
               ``KILL_SWITCH_ACTIVE``.                      - the kill switch this file's paper-route
                                                             test exercises is per-user
                                                             (``routers.risk``), and the
                                                             deployment-only global kill switch
                                                             (``core.global_safety.GlobalKillSwitch``,
                                                             checked inside
                                                             ``execute_with_idempotency``) sits on
                                                             the BLOCKED deployment path, not on
                                                             ``open_position``.
Daily loss     Enforced. ``realized_pnl`` vs               Enforced. ``RiskManager.can_trade()``'s
               ``max_daily_loss`` ->                        ``max_daily_loss_pct`` (5% of equity)
               ``MAX_DAILY_LOSS_EXCEEDED``.                  branch, reached through
                                                             ``can_open_position``.
Position size  Enforced (strategy-scoped notional cap) ->  Enforced (10% of equity) ->
               ``MAX_POSITION_SIZE_EXCEEDED``.               plain-text message, NOT a code -
                                                             see the gap below.
Leverage       NOT ENFORCED ANYWHERE REACHABLE HERE.        NOT ENFORCED ANYWHERE REACHABLE HERE.
============  =========================================  =========================================

TWO GAPS THIS FILE RECORDS RATHER THAN PAPERS OVER
-----------------------------------------------------
**Gap A - leverage has no reachable server-side enforcement at all (candidate new defect).**
``max_leverage`` exists in ``routers.risk.get_user_risk_settings_store`` /
``RiskSettingsUpdateRequest`` / the risk-metrics response body ONLY. It is never read by
``PaperTradingService.place_order`` (grepped: no ``leverage`` token anywhere in
``paper_trading_service.py``), and ``ExecutionEngine.open_position`` /
``RiskManager.can_open_position`` take no leverage parameter at all - there is no notion of
leverage on either mechanism this task can reach. The one place leverage IS enforced,
``InstitutionalRiskManager.validate_trade_request``'s tiered leverage-scaling check (Requirement
2.13's ``REJECT_LEVERAGE_SCALING``), is called from exactly one place in this tree:
``backend/master_executor.py:381``, inside the live per-tick worker loop - the deployment path.
So a trader's configured ``max_leverage`` is read back to them in
``GET /api/risk/settings`` and enforced only once a strategy is deployed and running, and never
on the paper route or in ``ExecutionEngine`` directly. Whether this is intentional (leverage is
a live/margin-account concept the paper simulator has no notion of) or a genuine hole depends on
whether the paper route is meant to simulate leveraged trading; it is reported here as a
candidate new defect for the requirements owner to triage, not assumed either way, and it is
NOT added to ``bugfix.md`` by this file.

**Gap B - the execution service's guardrails carry no code at all, only a formatted message.**
``ExecutionEngine.open_position`` / ``RiskManager.can_open_position`` return ``(bool, str)``,
where the ``str`` is one of three human-readable, emoji-prefixed sentences
(``"[SHIELD] MAX POSITION SIZE EXCEEDED | ..."``, the ``can_trade()`` daily-loss/drawdown
sentence, ``"[SHIELD] MAX OPEN TRADES EXCEEDED | ..."`` - shown here without the literal emoji).
There is no ``Enum``, no constant, nothing a caller can switch on other than the message
itself. This is unlike ``InstitutionalRiskManager.validate_trade_request``, twelve lines away in
the same file, which returns a proper ``RiskVerdict`` enum for the equivalent checks - and unlike
``dag_risk_integration.RiskDecision``, which is a real enum on the deployment path.
``ExecutionEngine``'s three branches ARE distinguishable from each other (each message has a
fixed, distinct leading phrase this file asserts against as the closest available signature),
but that is pattern-matching a sentence written for a print statement, not a machine-readable
code, and it is reported as a second candidate defect rather than treated as satisfying
Requirement 2.13's "distinct machine-readable code" on its own terms.

WHAT THIS FILE DOES NOT DO
-----------------------------
* Does not call ``POST /api/orders/execute`` or ``POST /api/orders/create``
  (``backend_app/routers/orders.py``) - per the task's own constraint, a pass there proves
  nothing, because both answer 403 ``MANUAL_EXECUTION_BLOCKED`` unconditionally. Neither route,
  nor ``createOrder``, is imported, called, or altered by this file.
* Does not exercise a live venue or a live fill, per the bugfix's "live-order testing against a
  real exchange is out of bounds" environment constraint.
* Does not attempt to prove the deployment path - see BLOCKED, below.

BLOCKED, GAP NAMED: THE DEPLOYMENT PATH
------------------------------------------
The only path by which an order reaches a venue is strategy deployment
(``core/live_engine.LiveTradingEngine`` and ``backend/master_executor.py``'s per-tick loop, both
of which run inside a live worker process against a real or exchange-simulated data feed), and
this environment cannot start or exercise that worker. Two consequences follow directly and are
recorded as BLOCKED rather than silently assumed:

1. The DEPLOYMENT-ONLY global kill switch (``core.global_safety.GlobalKillSwitch``, checked
   inside ``ExecutionEngine.execute_with_idempotency`` before the live executor is ever called)
   is not exercised here. It is a distinct mechanism from the per-user kill switch this file's
   ``test_kill_switch_blocks_paper_order`` exercises, and it needs the live/worker path this
   environment cannot start.
2. Leverage scaling's one enforcement point (``InstitutionalRiskManager.validate_trade_request``,
   called only from ``master_executor.py``'s worker loop) is not exercised here for the same
   reason - see Gap A above, which is the same underlying blocker restated as a coverage gap
   rather than a missing control, and reported both ways because it is possible to read it
   either way.

Provable here: **yes** for the paper route and the execution service, across the three violation
types either mechanism can enforce; **no** for leverage on either mechanism (Gap A); and
**partly** for the deployment path, which needs a running worker this environment does not have.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from backend_app.backend.paper import paper_repository as paper_repo
from backend_app.backend.paper_trading_service import get_paper_trading_service
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.routers import risk as risk_router
from tests.test_paper_repository import FakeSupabase


# ═══════════════════════════════════════════════════════════════════════════
# SHARED FIXTURES - THE PAPER ROUTE
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _paper_persistence():
    """A fresh in-memory Persistence_Layer, and a clean risk-settings module state.

    ``PaperTradingService.place_order`` reads and writes the ``paper_*`` tables through
    ``FakeSupabase`` - the one Persistence_Layer double this repository uses (see
    ``tests/paper_seed.py``'s own docstring for why there is exactly one). It also reads three
    module-level dicts in ``routers/risk.py`` (``_user_risk_settings``,
    ``_user_strategy_limits``, ``_user_kill_switch_state``) that no fixture in this tree resets,
    so a user id from an earlier test could otherwise leak a kill-switch or a strategy limit
    into this one. Each user id here is a fresh ``uuid4()`` for the same reason, belt-and-braces
    against that leak.
    """
    paper_repo.reset_persistence_probe()
    service = get_paper_trading_service()
    service.bind_persistence(FakeSupabase())
    try:
        yield service
    finally:
        service.bind_persistence(None)
        paper_repo.reset_persistence_probe()


#: The tables an order actually moves: the order itself, its fills, the position it would open
#: or adjust, and the balance ledger a lock or a fill writes to. Deliberately NOT
#: ``paper_accounts`` - ``get_or_create_account`` auto-creates a first-time user's account as a
#: side effect of the read every call to ``place_order`` starts with, identically whether the
#: order that follows is accepted or refused, so an insert there is a fact about "is this the
#: user's first call" and not about "was this order's refusal clean". Each test below
#: pre-creates the account and clears the statement log before submitting the violation, so this
#: helper's narrower table set and the account table's exclusion are two independent reasons the
#: assertion holds and not one masking the other.
_ORDER_WRITE_TABLES = (
    paper_repo.ORDERS_TABLE,
    paper_repo.FILLS_TABLE,
    paper_repo.POSITIONS_TABLE,
    paper_repo.BALANCE_EVENTS_TABLE,
)


def _wrote_any_order_table(supabase: FakeSupabase) -> List[Any]:
    """Every ``(op, table)`` issued against an order-lifecycle write table, for the assertion
    that none was. Mirrors ``tests/test_paper_order_lifecycle_writes.py``'s ``_wrote_since`` /
    ``wrote_anything`` convention, scoped to :data:`_ORDER_WRITE_TABLES` - see its docstring for
    why the account table itself is excluded.
    """
    return [
        s
        for s in supabase.statements
        if s.op in ("insert", "update") and s.table_name in _ORDER_WRITE_TABLES
    ]


def _captured_violation_codes(monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[str]]:
    """Patch ``routers.risk.record_risk_violation`` to capture the ``rule`` code it is called
    with, at its source, instead of parsing the human-readable ``ValueError`` message the
    service raises alongside it.

    ``paper_trading_service.place_order`` imports ``record_risk_violation`` with a
    function-local ``from backend_app.routers.risk import (...)`` on every call, which resolves
    the name from the module's namespace at call time - so patching
    ``backend_app.routers.risk.record_risk_violation`` is visible to it without patching
    ``paper_trading_service`` itself.
    """
    captured: Dict[str, List[str]] = {"rules": [], "reasons": []}

    def _capture(user_id: str, rule: str, reason: str, details: Any = None) -> None:
        captured["rules"].append(rule)
        captured["reasons"].append(reason)

    monkeypatch.setattr(risk_router, "record_risk_violation", _capture)
    return captured


async def _place(service: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("order_type", "market")
    return await service.place_order(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# 1. KILL SWITCH - the paper route
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_kill_switch_blocks_paper_order_with_distinct_code_and_no_write(
    _paper_persistence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill-switched user's order is refused with ``KILL_SWITCH_ACTIVE`` and writes nothing.

    Driven directly against ``PaperTradingService.place_order`` - no UI, no HTTP client, no
    ``/api/orders/execute`` or ``/api/orders/create`` involved. Per-user kill-switch state is set
    directly in ``routers.risk``'s module dict, which is exactly what
    ``POST /api/risk/kill-switch`` itself does (``_user_kill_switch_state[uid] = True``) -
    without the HTTP round trip that is not this task's concern.

    Validates: Requirements 1.13, 2.13.
    """
    service = _paper_persistence
    supabase: FakeSupabase = service._supabase
    uid = str(uuid4())
    captured = _captured_violation_codes(monkeypatch)

    # Created ahead of the violation attempt and off the statement log: a first-ever call
    # auto-creates the default account regardless of whether the order that follows is accepted
    # or refused, so that insert is not part of "no order row is written" (see
    # ``_ORDER_WRITE_TABLES``'s docstring).
    service.get_or_create_account(uid)

    risk_router._user_kill_switch_state[uid] = True
    try:
        supabase.statements.clear()

        with pytest.raises(ValueError):
            await _place(
                service, user_id=uid, symbol="BTC-USDT", side="buy", quantity=0.1, price=60000.0
            )

        assert captured["rules"] == ["KILL_SWITCH_ACTIVE"]
        assert _wrote_any_order_table(supabase) == []
    finally:
        risk_router._user_kill_switch_state.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════
# 2. DAILY LOSS - the paper route
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_paper_order_with_distinct_code_and_no_write(
    _paper_persistence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user already past their configured daily-loss limit is refused
    ``MAX_DAILY_LOSS_EXCEEDED`` and no order row is written.

    Validates: Requirements 1.13, 2.13.
    """
    service = _paper_persistence
    supabase: FakeSupabase = service._supabase
    uid = str(uuid4())
    captured = _captured_violation_codes(monkeypatch)

    risk_router._user_risk_settings[uid] = {
        **risk_router.get_user_risk_settings_store(uid),
        "max_daily_loss": 200.0,
    }

    # Seeding the realized loss the gate reads is itself a write, and the account's creation is
    # a second one - both are setup, not part of the order this test submits, so the statement
    # log is cleared only after both have happened (see ``_ORDER_WRITE_TABLES``'s docstring).
    account = service.get_or_create_account(uid)
    locked = paper_repo.lock_account_for_update(supabase, uid, account_id=account["account_id"])
    paper_repo.bump_version(
        supabase,
        user_id=uid,
        account_id=account["account_id"],
        expected_version=locked["version"],
        payload={"realized_pnl": "-250.00"},
    )
    supabase.statements.clear()

    with pytest.raises(ValueError):
        await _place(service, user_id=uid, symbol="ETH-USDT", side="buy", quantity=1.0, price=3000.0)

    assert captured["rules"] == ["MAX_DAILY_LOSS_EXCEEDED"]
    assert _wrote_any_order_table(supabase) == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. POSITION SIZE - the paper route (strategy-scoped notional cap)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_position_size_limit_blocks_paper_order_with_distinct_code_and_no_write(
    _paper_persistence: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An order whose notional exceeds the strategy's configured max position size is refused
    ``MAX_POSITION_SIZE_EXCEEDED`` and no order row is written.

    Validates: Requirements 1.13, 2.13.
    """
    service = _paper_persistence
    supabase: FakeSupabase = service._supabase
    uid = str(uuid4())
    strategy_id = "strat-server-side-12"
    captured = _captured_violation_codes(monkeypatch)

    service.get_or_create_account(uid)
    risk_router._user_strategy_limits[uid] = {
        strategy_id: {"max_position_size": 5000.0, "allowed_symbols": []}
    }
    supabase.statements.clear()

    # 0.1 BTC @ $60,000 = $6,000 notional, over the $5,000 strategy cap.
    with pytest.raises(ValueError):
        await _place(
            service,
            user_id=uid,
            strategy_id=strategy_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.1,
            price=60000.0,
        )

    assert captured["rules"] == ["MAX_POSITION_SIZE_EXCEEDED"]
    assert _wrote_any_order_table(supabase) == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. LEVERAGE - the paper route: NOT ENFORCED. Recorded, not invented.
# ═══════════════════════════════════════════════════════════════════════════

def test_leverage_is_not_read_by_the_paper_route() -> None:
    """There is no leverage check to submit a violation against on the paper route.

    This is not a passing enforcement test - it is the negative fact the module docstring's
    Gap A names, pinned so a future change that starts reading ``max_leverage`` inside
    ``PaperTradingService.place_order`` is noticed here rather than silently invalidating the
    docstring's claim. Grepping the source is the correct tool for a negative claim about what a
    function does NOT read: no fixture, double or request could distinguish "leverage was
    checked and passed" from "leverage was never looked at", so asserting on a call outcome
    would silently manufacture the very enforcement this test is recording the absence of.
    """
    import inspect

    from backend_app.backend import paper_trading_service as svc_module

    source = inspect.getsource(svc_module.PaperTradingService.place_order)
    assert "leverage" not in source.lower(), (
        "PaperTradingService.place_order now mentions leverage; Gap A in this file's module "
        "docstring is stale and the enforcement test it says is missing should be written."
    )


# ═══════════════════════════════════════════════════════════════════════════
# SHARED FIXTURE - THE EXECUTION SERVICE
# ═══════════════════════════════════════════════════════════════════════════

def _engine(*, equity: str = "100000.0") -> ExecutionEngine:
    """A bare, DB-free ``ExecutionEngine``, constructed the way
    ``backend_app/validate_financials.py`` already constructs one for its own checks - no
    worker, no Postgres, no FastAPI app.
    """
    return ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0,
        portfolio_state={"total_equity": Decimal(equity), "available_balance": Decimal(equity)},
    )


# ═══════════════════════════════════════════════════════════════════════════
# 5. POSITION SIZE - the execution service
# ═══════════════════════════════════════════════════════════════════════════

def test_position_size_limit_blocks_execution_engine_with_no_position_recorded() -> None:
    """A position whose notional exceeds 10% of equity is refused before any position exists.

    ``open_position`` returns ``(bool, str)`` rather than a code - see Gap B in the module
    docstring - so the closest available "distinct machine-readable" signature is the message's
    fixed leading phrase, asserted here rather than on the full sentence (which also carries the
    computed dollar figures and would make the assertion brittle for a reason unrelated to the
    property being tested).

    Validates: Requirements 1.13, 2.13.
    """
    engine = _engine(equity="100000.0")

    # 1 BTC @ $50,000 = $50,000 notional, over the $10,000 (10% of $100,000) cap.
    allowed, message = engine.open_position("BTC-USDT", Decimal("50000"), Decimal("1"), side="long")

    assert allowed is False
    assert "MAX POSITION SIZE EXCEEDED" in message
    assert engine.positions == {}
    assert engine._blocked_trades and engine._blocked_trades[-1]["symbol"] == "BTC-USDT"


# ═══════════════════════════════════════════════════════════════════════════
# 6. DAILY LOSS - the execution service
# ═══════════════════════════════════════════════════════════════════════════

def test_daily_loss_limit_blocks_execution_engine_with_no_position_recorded() -> None:
    """A daily loss already past 5% of equity refuses every new position, however small.

    ``RiskManager.update_equity`` is called directly to establish the day's loss - the same
    state ``core/live_engine.py``'s own risk manager accrues in its normal course of updating
    equity after each trade, driven here without a worker.

    Validates: Requirements 1.13, 2.13.
    """
    engine = _engine(equity="100000.0")
    # 6% realized loss today: over RiskThresholds.max_daily_loss_pct's default of 5%.
    engine.risk_manager.update_equity(-6000.0)

    # A trivially small position: proves the block is the daily-loss branch and not the
    # position-size branch, which a large notional could not distinguish.
    allowed, message = engine.open_position("ETH-USDT", Decimal("3000"), Decimal("0.001"), side="long")

    assert allowed is False
    assert "MAX DAILY LOSS HIT" in message
    assert engine.positions == {}


# ═══════════════════════════════════════════════════════════════════════════
# 7. MAX OPEN TRADES - the execution service (the guardrail the paper route has no equivalent
#    of; included because ExecutionEngine's own docstring names it as one of its three global
#    guardrails, and Requirement 1.13's "position size" clause is proven incompletely without it)
# ═══════════════════════════════════════════════════════════════════════════

def test_max_open_trades_limit_blocks_execution_engine_with_no_position_recorded() -> None:
    """A sixth concurrent position is refused once five are already open - the engine's own
    documented ``max_open_trades`` guardrail.

    Validates: Requirements 1.13, 2.13.
    """
    engine = _engine(equity="1000000.0")
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "AVAX-USDT", "LINK-USDT"]
    for symbol in symbols:
        opened, msg = engine.open_position(symbol, Decimal("100"), Decimal("1"), side="long")
        assert opened is True, f"setup failed opening {symbol}: {msg}"
    assert len(engine.positions) == len(symbols) == engine.risk_manager.max_open_trades

    allowed, message = engine.open_position("DOGE-USDT", Decimal("0.1"), Decimal("1"), side="long")

    assert allowed is False
    assert "MAX OPEN TRADES EXCEEDED" in message
    assert "DOGE-USDT" not in engine.positions
    assert len(engine.positions) == len(symbols)


# ═══════════════════════════════════════════════════════════════════════════
# 8. LEVERAGE - the execution service: NOT ENFORCED. Recorded, not invented.
# ═══════════════════════════════════════════════════════════════════════════

def test_leverage_is_not_a_parameter_of_open_position() -> None:
    """There is no leverage argument, and no leverage check, in ``ExecutionEngine.open_position``
    or ``RiskManager.can_open_position`` - the second half of Gap A.

    Same reasoning as ``test_leverage_is_not_read_by_the_paper_route``: this pins the absence so
    a future change that adds leverage handling here is noticed rather than silently
    invalidating the docstring's claim, instead of asserting a call outcome that cannot
    distinguish "checked and passed" from "never looked at".
    """
    import inspect

    from backend_app.core import execution_engine as engine_module
    from backend_app.core import risk_manager as risk_manager_module

    open_position_params = inspect.signature(engine_module.ExecutionEngine.open_position).parameters
    can_open_position_params = inspect.signature(
        risk_manager_module.InstitutionalRiskManager.can_open_position
    ).parameters

    assert "leverage" not in open_position_params
    assert "leverage" not in can_open_position_params
    assert "leverage" not in inspect.getsource(engine_module.ExecutionEngine.open_position).lower()
