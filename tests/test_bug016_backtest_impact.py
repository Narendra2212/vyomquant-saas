"""What BUG-016's accounting fix does and does not change about backtest results.

THE QUESTION THIS FILE ANSWERS
------------------------------
BUG-016 changed how ``ExecutionEngine`` accounts for cash and equity. The brief for the
remediation cycle requires that stored results are never silently presented as though
they came from the corrected engine. So: which backtest results move, and is anything
persisted now stale?

THE ANSWER, established by tracing both paths rather than assuming they share an engine:

**Path A — ``routers/strategies.backtest_internal``** uses ``ExecutionEngine`` and IS
affected. It is reachable three ways: the synchronous backtest route, the queued
``backend_app/worker.py`` consumer, and the ``optimize_strategy`` /
``monte_carlo_simulation`` endpoints. Its results are written to **Redis job status
only** (``_write_status(..., result=json.dumps(result))``) — ephemeral, keyed by job id,
with no durable record and nothing in the marketplace reading it.

**Path B — ``backend/backtest_runtime``** is what persists to ``strategy_backtests`` and
produces the marketplace's performance evidence. It contains **zero** references to
``ExecutionEngine`` and runs on vectorbt. It is therefore **unaffected**.

So the engine-version question resolves by SEPARATION rather than by versioning: the
corrected engine does not feed any persisted result, so there is nothing to invalidate or
recompute. ``test_the_persisted_backtest_path_does_not_use_the_execution_engine`` is what
keeps that true — if someone later points ``backtest_runtime`` at ``ExecutionEngine``,
the separation argument stops holding and this test says so.

WHAT ACTUALLY CHANGES ON PATH A
-------------------------------
Only a backtest that **ends with a position still open**. A fully-closed backtest reports
the identical figure (proven in ``test_execution_engine_accounting_identity.py``). A
backtest holding an open position used to report a return that omitted that position
entirely, because equity moved only on a realised close. It now includes it.

That is a correction in the direction of truth, and it is a change in reported numbers,
so it is documented here rather than left for someone to discover.
"""

from decimal import Decimal

import pytest

from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.core.execution_engine import ExecutionEngine

CAPITAL = Decimal("100000")


def _backtest_engine():
    """Constructed the way ``backtest_internal`` constructs it, minus the slippage
    randomness so the assertion is a number rather than a range."""
    return ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0,
        portfolio_state={
            "total_equity": str(CAPITAL),
            "available_balance": str(CAPITAL),
            "total_exposure": "0",
            "positions": {},
            "daily_pnl": "0",
        },
        execution_environment=ExecutionEnvironment.BACKTEST,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. The persisted path is insulated
# ═══════════════════════════════════════════════════════════════════════════

def test_the_persisted_backtest_path_does_not_use_the_execution_engine():
    """THE LOAD-BEARING ASSERTION of the no-invalidation argument.

    ``backtest_runtime`` is what writes ``strategy_backtests`` and feeds marketplace
    evidence. As long as it does not construct ``ExecutionEngine``, BUG-016 cannot have
    changed a single persisted figure. If that ever stops being true, the stored results
    become stale and this test is the warning.
    """
    import ast
    import inspect

    from backend_app.backend import backtest_runtime

    tree = ast.parse(inspect.getsource(backtest_runtime))
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "ExecutionEngine" not in constructed, (
        "backtest_runtime now constructs ExecutionEngine, so BUG-016's accounting "
        "change reaches persisted strategy_backtests rows and previously stored "
        "results are stale. They must be versioned or recomputed."
    )
    assert "UnifiedExecutionEngine" not in constructed


def test_the_affected_path_persists_nothing_durable():
    """``backtest_internal``'s result reaches Redis job status and stops there.

    Asserted on the worker's own code: if a durable write is added, the
    "nothing to invalidate" conclusion no longer holds.
    """
    import ast
    import inspect

    from backend_app import worker

    source = inspect.getsource(worker)
    tree = ast.parse(source)

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    # The result goes to _write_status (Redis). No table/insert/upsert call.
    assert "_write_status" in {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | called

    for durable in ("insert", "upsert"):
        assert durable not in called, (
            f"the backtest worker now performs a durable {durable!r}; "
            f"backtest_internal results are no longer ephemeral and BUG-016's change "
            f"would make previously stored rows stale"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. What changes, stated as a measurement
# ═══════════════════════════════════════════════════════════════════════════

def test_a_fully_closed_backtest_reports_the_same_equity_as_before():
    """No change. This is the common case and the reason most results are unaffected."""
    eng = _backtest_engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("52000"))

    assert eng.current_equity == CAPITAL + pnl
    assert eng.cash == eng.current_equity


def test_a_backtest_ending_with_an_open_position_now_includes_it():
    """THE CHANGE. Equity used to omit the open position entirely.

    Old behaviour: equity = capital - entry_fee, whatever the position was worth.
    New behaviour: equity = cash + marked position value.
    """
    eng = _backtest_engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    fee = Decimal("50000") * Decimal("0.1") * Decimal("0.001")

    # The position doubles and the backtest ends without closing it.
    eng.mark_to_market("BTCUSDT", Decimal("100000"))

    old_behaviour_equity = CAPITAL - fee          # what the engine used to report
    gain = (Decimal("100000") - Decimal("50000")) * Decimal("0.1")   # 5,000

    assert eng.current_equity == old_behaviour_equity + gain, (
        "an unrealised gain on a position still open at the end of the backtest is not "
        "reflected in equity"
    )
    assert eng.current_equity != old_behaviour_equity, (
        "equity is unchanged from the old fee-only behaviour, so the open position is "
        "still being omitted"
    )


def test_an_open_losing_position_is_also_included():
    """The correction cuts both ways, which is the point — it is not a flattering
    change, it is an accurate one."""
    eng = _backtest_engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    fee = Decimal("50000") * Decimal("0.1") * Decimal("0.001")

    eng.mark_to_market("BTCUSDT", Decimal("25000"))     # -50%

    loss = (Decimal("25000") - Decimal("50000")) * Decimal("0.1")   # -2,500
    assert eng.current_equity == CAPITAL - fee + loss
    assert eng.current_equity < CAPITAL - fee, (
        "an open loss is not reflected, so a backtest could report a flat result while "
        "holding a position down 50%"
    )


def test_the_reported_return_percentage_changes_only_for_open_ended_backtests():
    """Expressed as the figure a user actually reads on the Backtester screen."""
    closed = _backtest_engine()
    closed.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    closed.close_position("BTCUSDT", Decimal("52000"))
    closed_return = float((closed.current_equity / CAPITAL - Decimal(1)) * 100)

    open_ended = _backtest_engine()
    open_ended.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    open_ended.mark_to_market("BTCUSDT", Decimal("52000"))
    open_return = float((open_ended.current_equity / CAPITAL - Decimal(1)) * 100)

    # Same price move, same size. The only difference is whether it was realised.
    # Both should now be positive; previously only the closed one was.
    assert closed_return > 0
    assert open_return > 0, (
        "an open-ended backtest with a winning position still reports a non-positive "
        "return, so the position is being omitted"
    )
    # They differ only by the exit fee that the open position has not paid yet.
    exit_fee = Decimal("52000") * Decimal("0.1") * Decimal("0.001")
    assert (open_return - closed_return) == pytest.approx(
        float(exit_fee / CAPITAL * 100), abs=1e-9
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. The statistics block a consumer reads
# ═══════════════════════════════════════════════════════════════════════════

def test_statistics_expose_cash_and_position_value_separately():
    """So a consumer can verify the identity instead of inferring it from one number."""
    eng = _backtest_engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    eng.mark_to_market("BTCUSDT", Decimal("55000"))

    stats = eng.get_stats()

    assert stats["cash"] == pytest.approx(float(eng.cash))
    assert stats["position_market_value"] == pytest.approx(float(eng.position_market_value()))
    assert stats["current_equity"] == pytest.approx(
        stats["cash"] + stats["position_market_value"]
    )
    assert stats["open_positions"] == 1
    assert stats["total_exposure"] == pytest.approx(5500.0)
