"""tests/test_portfolio_manager_requires_real_capital_regression.py

The portfolio manager is not conjured with a capital figure nobody chose.

THE DEFECT
----------
``get_portfolio_manager()`` lazily built ``PortfolioManager(total_capital=Decimal("100000"))``
whenever no portfolio had been initialised, and fifteen endpoints in
``backend_app/backend/portfolio_management.py`` call it. Separately, the one endpoint whose
job is to establish real capital - ``POST /initialize`` - declared
``total_capital: Decimal = Decimal(\x27100000.0\x27)``, so calling it with no argument invented the
same figure.

WHY THIS IS NOT A DISPLAY BUG
-----------------------------
``total_capital`` is the denominator and the ceiling inside ``PortfolioManager``:

  * ``register_strategy`` derives allocated_capital and available_capital from it;
  * ``check_position_limits`` derives max_symbol_exposure_pct, max_total_exposure_pct and the
    gross-leverage check from it;
  * ``get_margin_metrics`` derives margin_available, margin_utilization_pct and margin_level;
  * ``get_pnl_summary`` derives every percentage;
  * equity is ``total_capital + unrealised pnl``.

So an invented 100k WIDENED REAL RISK LIMITS rather than merely printing a wrong number. A
trader with 2k of capital had exposure ceilings computed for fifty times that. Same defect
class as the ExecutionEngine one fixed alongside it.

WHY IT RAISES INSTEAD OF DEFAULTING TO ZERO
-------------------------------------------
Zero is no less invented, and collapsing not-initialised into zero is the failure mode the
launch-hardening bugfix names. One raise covers all fifteen endpoints, because ``main.py``
already installs a global HTTPException handler.
"""

import inspect
import io
import os
import re
import sys
from decimal import Decimal

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import portfolio_management as PM

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODULE = "backend_app/backend/portfolio_management.py"


@pytest.fixture(autouse=True)
def no_portfolio():
    """Each test starts with no initialised portfolio, and the global is restored after."""
    saved = PM._portfolio_manager
    PM._portfolio_manager = None
    try:
        yield
    finally:
        PM._portfolio_manager = saved


def invented_capital_calls():
    """
    Every Decimal(100000...) CALL in the module, found by AST rather than by text.

    Two earlier versions of this check were vacuous and both are worth remembering. A regex
    with doubled backslashes matched a literal backslash, so it passed against the UNFIXED
    code. A plain substring scan then failed against the FIXED code, because the explanatory
    docstring in get_portfolio_manager quotes the old value.

    An AST walk has neither problem: a docstring is a bare Constant expression and never a
    Call, so prose can neither satisfy nor trip this.
    """
    import ast

    tree = ast.parse(io.open(os.path.join(REPO, MODULE), encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "Decimal":
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and str(arg.value).startswith("100000"):
            found.append("line %d: Decimal(%r)" % (node.lineno, arg.value))
    return found

class TestAnUninitialisedPortfolioIsRefused:
    def test_it_raises_instead_of_building_one(self):
        """Was: returned a PortfolioManager carrying 100000 nobody established."""
        with pytest.raises(HTTPException) as excinfo:
            PM.get_portfolio_manager()

        assert excinfo.value.status_code == 503

    def test_the_refusal_carries_a_machine_readable_code(self):
        with pytest.raises(HTTPException) as excinfo:
            PM.get_portfolio_manager()

        detail = excinfo.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == "PORTFOLIO_NOT_INITIALISED"

    def test_it_does_not_leave_a_manager_behind(self):
        """A refusal must not half-create the singleton it declined to build."""
        with pytest.raises(HTTPException):
            PM.get_portfolio_manager()

        assert PM._portfolio_manager is None


class TestRealCapitalIsHonoured:
    def test_an_initialised_portfolio_is_returned_unchanged(self):
        PM._portfolio_manager = PM.PortfolioManager(total_capital=Decimal("2000"))

        manager = PM.get_portfolio_manager()

        assert manager.total_capital == Decimal("2000")

    def test_a_small_account_is_not_promoted_to_100k(self):
        """The user-visible consequence, stated as an assertion."""
        PM._portfolio_manager = PM.PortfolioManager(total_capital=Decimal("2000"))

        manager = PM.get_portfolio_manager()

        assert manager.total_capital != Decimal("100000")
        assert manager.total_capital < Decimal("100000")


class TestTheInitialiserRequiresItsFigure:
    def test_total_capital_has_no_default(self):
        """POST /initialize invented 100000.0 when called with no argument."""
        param = inspect.signature(PM.initialize_portfolio).parameters["total_capital"]

        assert param.default is inspect.Parameter.empty, (
            "initialize_portfolio still defaults total_capital to %r; the one endpoint whose "
            "job is to establish real capital must not invent it" % (param.default,)
        )


class TestTheLiteralIsGoneFromExecutableCode:
    def test_no_invented_capital_remains(self):
        offenders = invented_capital_calls()

        assert offenders == [], (
            "portfolio_management.py still constructs an invented capital figure: %s"
            % offenders
        )
