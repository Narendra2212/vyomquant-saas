"""tests/test_reduce_only_not_gated_on_capital_regression.py

A cancel or a close is not gated on whether the account can afford a position.

THE DEFECT
----------
``cancel_order``, ``cancel_all`` and ``close_all_positions`` each ran the FULL
``ExecutionGuard.validate_trade`` suite, six of whose sixteen checks answer one question:
can this account afford to take on this position. On a risk-REDUCING operation that
question is meaningless, and both possible answers are wrong:

  * against the fabricated equity the portfolio fallback supplied - ``get_portfolio_state``
    fell back to ``get_portfolio_manager()``, which lazily builds a PortfolioManager with
    ``total_capital=Decimal("100000")`` - every check PASSED. A rubber stamp on a figure
    nobody read, stamped ``cached_at: time.time()`` as though freshly fetched.
  * against an honest absence they BLOCK: ``_validate_portfolio_concentration`` refuses
    ``total_equity <= 0`` outright, and ``_validate_sufficient_balance`` reads
    ``available_balance`` as 0 and reports insufficient balance. That stops a trader
    cancelling an order or closing a position - worse than not checking, because it leaves
    them exposed in a moving market.

So neither fixing nor keeping the fabrication was the answer. ``reduce_only=True`` skips
exactly those six and nothing else.

WHAT IS DELIBERATELY STILL ENFORCED
-----------------------------------
Removing the guard from these paths would have been wrong: it carries duplicate-order
idempotency (submitting the same cancel twice), the tenant symbol allow-list, and the
circuit breakers that are the kill switch. None of those is a function of capital, and all
three still run. ``test_a_reduce_only_operation_still_runs_the_checks_that_matter`` pins it.
"""

import asyncio
import inspect
import io
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.execution_guard import (ExecutionGuard,
                                                 ValidationResult,
                                                 ValidationSeverity)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The six that require real capital, by the method name on ExecutionGuard.
CAPITAL_CHECKS = [
    "_validate_sufficient_balance",
    "_validate_position_and_exposure_limits",
    "_validate_portfolio_concentration",
    "_validate_exposure_limits",
    "_validate_daily_drawdown",
    "_validate_composite_risk_score",
]

#: The ones that must survive a reduce-only operation, because none is about capital.
MUST_STILL_RUN = [
    "_validate_no_duplicate_order",
    "_validate_symbol_allowed",
    "_validate_circuit_breakers",
    "_validate_signal_basic",
    "_validate_order_size",
]

SIGNAL = {"symbol": "BTC/USDT", "side": "sell", "size": "0.5", "price": "60000",
          "trade_id": "t-1", "signal_id": "s-1"}
MARKET = {"spread_bps": "10", "volatility": "0.02", "status": "open"}

#: What an unread portfolio honestly looks like.
UNESTABLISHED = {}


@pytest.fixture
def guard(monkeypatch):
    """A real ExecutionGuard whose sixteen checks are recorded instead of executed.

    Every check is replaced with a passing result, so what is under test is WHICH checks
    the method selects - not redis, not the individual check bodies.
    """
    g = ExecutionGuard(redis_client=None)
    called = []

    def make(name):
        async def recorded(*a, **kw):
            called.append(name)
            return ValidationResult(
                check_name=name, passed=True, severity=ValidationSeverity.PASS,
                message="recorded",
            )
        return recorded

    for name in dir(g):
        if name.startswith("_validate_"):
            monkeypatch.setattr(g, name, make(name))

    g.called = called
    return g


def _run(g, reduce_only):
    return asyncio.run(g.validate_trade(
        tenant_id="tenant-1", signal=SIGNAL,
        portfolio_state=UNESTABLISHED, market_state=MARKET,
        reduce_only=reduce_only,
    ))


class TestReduceOnlySkipsOnlyTheCapitalChecks:
    def test_the_six_capital_checks_are_not_run(self, guard):
        _run(guard, reduce_only=True)

        ran = set(guard.called)
        leaked = sorted(ran.intersection(CAPITAL_CHECKS))
        assert leaked == [], (
            "a reduce-only operation still ran capital checks %s; against an unread "
            "portfolio those block the cancel" % leaked
        )

    def test_the_checks_that_matter_still_run(self, guard):
        """Removing the guard wholesale would have dropped the kill switch."""
        _run(guard, reduce_only=True)

        ran = set(guard.called)
        missing = sorted(set(MUST_STILL_RUN) - ran)
        assert missing == [], (
            "a reduce-only operation skipped %s; idempotency, the symbol allow-list and "
            "the circuit-breaker kill switch are not functions of capital and must "
            "still apply" % missing
        )

    def test_an_opening_trade_still_runs_every_check(self, guard):
        """Preservation: nothing changes for the path that actually takes on risk."""
        _run(guard, reduce_only=False)

        ran = set(guard.called)
        missing = sorted(set(CAPITAL_CHECKS) - ran)
        assert missing == [], (
            "an opening trade skipped capital checks %s" % missing
        )

    def test_the_default_is_false(self):
        """Every pre-existing caller keeps the full suite without being touched."""
        param = inspect.signature(ExecutionGuard.validate_trade).parameters["reduce_only"]

        assert param.default is False


class TestThePremiseAnHonestReadWouldHaveBlocked:
    def test_concentration_blocks_on_unestablished_equity(self):
        """Why an honest absence was not enough on its own.

        This is the real check, not a double. It is the reason removing the fabrication
        without reduce_only would have trapped traders.
        """
        g = ExecutionGuard(redis_client=None)

        result = asyncio.run(g._validate_portfolio_concentration({}, SIGNAL))

        assert result.passed is False
        assert result.severity is ValidationSeverity.BLOCK

    def test_balance_blocks_on_unestablished_equity(self):
        g = ExecutionGuard(redis_client=None)

        result = asyncio.run(g._validate_sufficient_balance({}, SIGNAL))

        assert result.passed is False
        assert result.severity is ValidationSeverity.BLOCK


class TestTheThreeRiskReducingRoutesOptIn:
    @pytest.mark.parametrize(
        "rel,count",
        [("backend_app/routers/orders.py", 2),
         ("backend_app/routers/portfolio.py", 1)],
    )
    def test_every_guard_call_on_these_routes_is_reduce_only(self, rel, count):
        """cancel, cancel-all and close-all: three call sites, all opted in."""
        text = io.open(os.path.join(REPO, rel), encoding="utf-8").read()

        assert text.count("reduce_only=True") == count, (
            "%s has %d reduce_only=True, expected %d - a risk-reducing route that "
            "still runs the capital checks is blocked whenever equity cannot be read"
            % (rel, text.count("reduce_only=True"), count)
        )
