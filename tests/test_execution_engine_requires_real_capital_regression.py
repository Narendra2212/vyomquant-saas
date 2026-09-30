"""tests/test_execution_engine_requires_real_capital_regression.py

The execution path does not invent capital to size real positions against.

THE DEFECT
----------
``ExecutionEngine.__init__`` documents, in its own docstring:

    CRITICAL: portfolio_state is REQUIRED for real money trading.
    No hardcoded capital is allowed - must use real portfolio data.
    Raises: ValueError: If portfolio_state is not provided or missing required fields

It did the opposite. An absent ``portfolio_state`` was replaced, at INFO level, with
``{total_equity: 100000.0, available_balance: 100000.0}``, and a present-but-incomplete one
had ``total_equity`` set to ``100000.0``. Three callers on the path they each label the
*Canonical ExecutionEngine Gateway* then passed that same literal deliberately:

    dag_risk_integration.execute_order          metadata default 100000.0
    distributed_execution/execution_worker      hardcoded 100000.0
    distributed_execution/idempotent_exchange_submission   hardcoded 100000.0

WHY IT IS SEVERE. ``total_equity`` becomes ``initial_capital``, ``current_equity``,
``peak_equity`` AND ``RiskManager(initial_equity=)``, so the position-size, exposure and
drawdown guardrails were all measured against 100k the account may never have held. A trader
with 2k of real capital would be authorised positions sized for fifty times that. This is the
fabrication class ``bugfix.md`` names - asked for a figure it does not have, the system
answered with an invented one - except here it sizes orders rather than filling a dashboard.

A SYNTHETIC BALANCE IS STILL LEGAL, BUT IT MUST BE CHOSEN. ``master_executor`` passes
``{total_equity: 10000.0}`` for its paper branch and says so in a comment; that still works.
What is refused is arriving with nothing and being handed a number nobody picked.
"""

import io
import os
import re
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.execution_engine import ExecutionEngine

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

CALLERS = [
    "backend_app/core/execution_engine.py",
    "backend_app/backend/dag_risk_integration.py",
    "backend_app/backend/distributed_execution/execution_worker.py",
    "backend_app/backend/distributed_execution/idempotent_exchange_submission.py",
]


def executable_source(rel):
    """
    The file source with ``#`` comments blanked, and NOTHING else blanked.

    Comments must go: the fix left explanatory comments that QUOTE the old literal, so a
    raw scan would fail against the very change it checks.

    String literals must STAY. The fabricated value is written ``Decimal("100000.0")``, so
    the number lives inside a string; blanking literals made this scan vacuous - it passed
    against the unfixed code, which is how the flaw was caught.
    """
    text = io.open(os.path.join(REPO, rel), encoding="utf-8").read()
    out = []
    for line in text.split(chr(10)):
        out.append(line.split("#")[0])
    return chr(10).join(out)

class TestAbsentCapitalIsRefusedNotInvented:
    def test_no_portfolio_state_raises(self):
        """Was: logged INFO and substituted 100000.0."""
        with pytest.raises(ValueError) as excinfo:
            ExecutionEngine()

        assert "portfolio_state" in str(excinfo.value)

    def test_empty_portfolio_state_raises(self):
        with pytest.raises(ValueError):
            ExecutionEngine(portfolio_state={})

    def test_portfolio_state_without_total_equity_raises(self):
        """Was: total_equity silently set to 100000.0."""
        with pytest.raises(ValueError) as excinfo:
            ExecutionEngine(portfolio_state={"available_balance": Decimal("2000")})

        assert "total_equity" in str(excinfo.value)

    def test_the_refusal_names_the_keys_it_was_given(self):
        """An operator must be able to see WHAT arrived, not just that it was wrong."""
        with pytest.raises(ValueError) as excinfo:
            ExecutionEngine(portfolio_state={"available_balance": "1", "daily_pnl": "0"})

        message = str(excinfo.value)
        assert "available_balance" in message and "daily_pnl" in message


class TestAnExplicitBalanceStillWorks:
    def test_a_chosen_synthetic_paper_balance_is_accepted(self):
        """master_executor paper branch passes {total_equity: 10000.0} on purpose."""
        engine = ExecutionEngine(portfolio_state={"total_equity": 10000.0})

        assert engine.initial_capital == Decimal("10000.0")

    def test_every_derived_figure_comes_from_the_supplied_equity(self):
        """The reason the default was dangerous: four things are derived from this one value."""
        engine = ExecutionEngine(portfolio_state={"total_equity": Decimal("2000")})

        assert engine.initial_capital == Decimal("2000")
        assert engine.current_equity == Decimal("2000")
        assert engine.peak_equity == Decimal("2000")
        assert Decimal(str(engine.risk_manager.initial_equity)) == Decimal("2000")

    def test_a_small_account_is_not_silently_promoted_to_100k(self):
        """The user-visible consequence, stated as an assertion."""
        engine = ExecutionEngine(portfolio_state={"total_equity": Decimal("2000")})

        assert engine.initial_capital != Decimal("100000.0")
        assert engine.initial_capital < Decimal("100000.0")


class TestNoCallerCarriesTheLiteralAnyMore:
    @pytest.mark.parametrize("rel", CALLERS)
    def test_the_fabricated_equity_is_gone_from_executable_code(self, rel):
        """Structural. Comments may still explain it; code may not still do it."""
        body = executable_source(rel)

        assert "100000.0" not in body, (
            "%s still has an executable 100000.0; the fabricated capital is back" % rel
        )

    @pytest.mark.parametrize(
        "rel",
        CALLERS[1:],  # the three gateway callers, not the engine itself
    )
    def test_each_gateway_refuses_rather_than_defaulting(self, rel):
        # RAW source here, not executable_source: the refusal code is itself a string
        # literal, and executable_source blanks literals so the 100000.0 scan above
        # cannot be satisfied by a comment quoting the old value.
        body = io.open(os.path.join(REPO, rel), encoding="utf-8").read()

        assert "PORTFOLIO_STATE_REQUIRED" in body, (
            "%s no longer signals a refusal when capital is absent, so it is either "
            "inventing one again or proceeding without it" % rel
        )
