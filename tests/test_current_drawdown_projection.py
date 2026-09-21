"""
tests/test_current_drawdown_projection.py - vyomquant-ui-redesign BC-1 (task 12.1).

Requirements 3.1, 10.2, 19.1, 19.2. `design.md` §1.5, §16.

WHAT THIS FILE EXISTS FOR
-------------------------
`dashboard_aggregation_service.get_risk_data` published `current_drawdown_pct` from
`today_return_pct`, and the shadowed older body from `abs(portfolio["pnl_pct"])`. Both are
returns, not drawdowns, so a **profitable** day rendered as a positive "drawdown" and `abs()`
of a gain rendered a gain as a loss. `routers/risk.py::get_risk_status` returned a literal
`"drawdown_pct": 0.0`, which asserts "no drawdown" about an account nothing measured. A trader
reads drawdown to decide whether to cut size.

BC-1 adds `current_drawdown_pct_v2`, the peak-to-trough decline computed from the equity series
the aggregation service already reads, and leaves the old field in place for its deprecation
window - it is an additive read projection (Requirement 19.1).

The load-bearing test here is `test_a_profitable_day_has_zero_drawdown_not_a_positive_figure`:
that is the regression this task exists to prevent. The rest fix the boundaries - a series that
only rose is at its peak (`0`), and a series too short to contain a decline reports `null`, not
`0` (Requirement 19.2: no fabricated figures).
"""

import ast
import inspect
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend_app.backend.dashboard_aggregation_service import (
    DashboardAggregationService,
    current_drawdown_pct_from_equity_curve,
)


def _risk_router_import_error():
    """Why `backend_app.routers.risk` cannot be imported here, or `None` if it can.

    `get_risk_data` imports that module inside its body, so the two projection tests in section
    3 need it. On an installed FastAPI whose `APIRouter` no longer accepts `on_startup`, the
    module raises at import - which is also why four tests in
    `tests/test_dashboard_phase1_contract.py` fail before this task touched anything. The
    condition is probed rather than assumed, so those tests run wherever the app does import and
    say plainly why they did not when it does not. They are never reported as passing on a run
    that did not execute them.
    """
    try:
        import backend_app.routers.risk  # noqa: F401
    except Exception as exc:  # the reason is the useful part, so it is carried into the skip
        return f"{type(exc).__name__}: {exc}"
    return None


_RISK_IMPORT_ERROR = _risk_router_import_error()

requires_risk_router = pytest.mark.skipif(
    _RISK_IMPORT_ERROR is not None,
    reason=(
        "backend_app.routers.risk is not importable in this environment, and "
        "get_risk_data imports it: " + str(_RISK_IMPORT_ERROR)
    ),
)


@pytest.fixture
def mock_user():
    return {
        "id": "test_user_bc1_drawdown",
        "email": "trader@vyomquant.com",
        "access_token": "valid_jwt_token",
    }


@pytest.fixture
def dashboard_service():
    return DashboardAggregationService()


def _curve(*equities):
    """An equity curve in the shape ``get_equity_curve`` returns it.

    The QuestDB read is ``SELECT timestamp, equity FROM equity_curve ... ORDER BY timestamp
    ASC``, so the rows are ``{"timestamp": ..., "equity": ...}`` in ascending time order. The
    timestamps here are ordered but otherwise arbitrary: the computation reads the series as
    given and does not sort it.
    """
    return [
        {"timestamp": f"2024-05-01T{hour:02d}:00:00Z", "equity": equity}
        for hour, equity in enumerate(equities)
    ]


# ══════════════════════════════════════════════════════════════════════════
# 1. THE COMPUTATION  (Requirements 3.1, 10.2)
# ══════════════════════════════════════════════════════════════════════════


def test_rising_then_falling_series_reports_the_peak_to_trough_decline():
    """100k → 120k → 90k is a 25% drawdown: 30k off a 120k peak, not off the 100k open.

    The peak is the maximum over the series, not its first point, so the decline is measured
    from where the account actually stood at its best.
    """
    assert current_drawdown_pct_from_equity_curve(
        _curve(100_000.0, 120_000.0, 90_000.0)
    ) == 25.0


def test_a_profitable_day_has_zero_drawdown_not_a_positive_figure():
    """THE REGRESSION THIS TASK EXISTS TO PREVENT (design.md §1.5).

    A series that only rose is sitting at its peak, so nothing has been given back and the
    drawdown is exactly zero. The old field would have published this same day as a positive
    "drawdown" of 20.0 - `today_return_pct` for a 20% gain - telling a trader to consider
    cutting size on their best day.
    """
    profitable_day = _curve(100_000.0, 110_000.0, 120_000.0)

    assert current_drawdown_pct_from_equity_curve(profitable_day) == 0.0

    # Stated explicitly, because "not the return figure" is the actual claim: the return over
    # this series is +20%, and the drawdown must not be that number under any sign.
    return_pct = (
        (profitable_day[-1]["equity"] - profitable_day[0]["equity"])
        / profitable_day[0]["equity"]
        * 100
    )
    assert return_pct == 20.0
    assert current_drawdown_pct_from_equity_curve(profitable_day) != return_pct
    assert current_drawdown_pct_from_equity_curve(profitable_day) != abs(return_pct)


def test_drawdown_is_never_negative():
    """A gain is no drawdown, not a negative one. Drawdown is a non-negative magnitude."""
    only_ever_rose = current_drawdown_pct_from_equity_curve(_curve(50_000.0, 250_000.0))

    assert only_ever_rose == 0.0
    assert only_ever_rose >= 0.0


def test_recovery_past_the_old_peak_is_zero_not_the_dip_it_recovered_from():
    """*Current* drawdown, not *maximum*: a series back at a new high has none outstanding.

    100k → 120k → 90k → 130k suffered a 25% decline on the way, but 130k is a new peak and
    nothing is outstanding now. This is what distinguishes this figure from
    `paper_accounting.max_drawdown`, which answers the historical-worst question instead.
    """
    assert current_drawdown_pct_from_equity_curve(
        _curve(100_000.0, 120_000.0, 90_000.0, 130_000.0)
    ) == 0.0


# ══════════════════════════════════════════════════════════════════════════
# 2. WHAT CANNOT BE MEASURED IS null, NOT ZERO  (Requirement 19.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "series,why",
    [
        (None, "no series was read at all"),
        ([], "the series is empty, so nothing was read"),
        ([{"timestamp": "2024-05-01T00:00:00Z", "equity": 100_000.0}],
         "one observation describes no decline - a peak needs something to fall from"),
    ],
)
def test_a_series_too_short_to_contain_a_decline_reports_null(series, why):
    """Absent, empty and single-point series are `None`.

    `0.0` here would claim the account is at its peak, which is a measurement nobody took.
    """
    assert current_drawdown_pct_from_equity_curve(series) is None, why


def test_a_non_positive_peak_reports_null_rather_than_a_ratio_of_zero():
    """A decline cannot be expressed as a fraction of a peak that is zero or below."""
    assert current_drawdown_pct_from_equity_curve(_curve(0.0, 0.0)) is None
    assert current_drawdown_pct_from_equity_curve(_curve(-500.0, -900.0)) is None


def test_an_unreadable_point_reports_null_rather_than_measuring_a_shorter_series():
    """A point with no equity makes the series unreadable, because it may have been the peak.

    Dropping it would silently compute the drawdown of a different series and report that
    number as though it were this one's.
    """
    with_a_hole = _curve(100_000.0, 120_000.0, 90_000.0)
    with_a_hole[1]["equity"] = None

    assert current_drawdown_pct_from_equity_curve(with_a_hole) is None


def test_zero_equity_is_a_real_reading_and_a_total_drawdown():
    """A wiped account is 100% down, not unreadable - zero equity is a measurement.

    This is the case the figure matters most for, so it must not be confused with the absent
    reading `test_an_unreadable_point_...` covers.
    """
    assert current_drawdown_pct_from_equity_curve(_curve(100_000.0, 0.0)) == 100.0


# ══════════════════════════════════════════════════════════════════════════
# 3. THE PROJECTION IS ADDITIVE  (Requirement 19.1)
# ══════════════════════════════════════════════════════════════════════════


@requires_risk_router
@pytest.mark.asyncio
async def test_risk_projection_carries_v2_alongside_the_unchanged_old_field(
    mock_user, dashboard_service
):
    """`current_drawdown_pct_v2` is added; `current_drawdown_pct` keeps its old value.

    The deprecated field still reports `today_return_pct` - here a +20% day - and the new one
    reports the real drawdown of the same series. Both are present, so no consumer of the old
    field is repointed by this change.
    """
    portfolio = {"today_return_pct": 20.0}
    equity_curve = _curve(100_000.0, 150_000.0, 120_000.0)

    with patch.object(
        DashboardAggregationService, "get_open_positions", new_callable=AsyncMock
    ) as mock_positions:
        mock_positions.return_value = []

        risk = await dashboard_service.get_risk_data(
            mock_user,
            environment="paper",
            portfolio=portfolio,
            equity_curve=equity_curve,
        )

    assert risk["current_drawdown_pct"] == 20.0, "the deprecated field must not change value"
    assert risk["current_drawdown_pct_v2"] == 20.0, "30000 off a 150000 peak"

    # The two agreeing numerically here is a coincidence of this fixture, not the contract. The
    # profitable-day case is where they diverge, and that is the one that matters.
    flat_day = await _v2_only(dashboard_service, mock_user, {"today_return_pct": 12.5},
                             _curve(80_000.0, 90_000.0))
    assert flat_day["current_drawdown_pct"] == 12.5
    assert flat_day["current_drawdown_pct_v2"] == 0.0


async def _v2_only(service, user, portfolio, equity_curve):
    """`get_risk_data` with the positions read stubbed, so no Redis or QuestDB is needed."""
    with patch.object(
        DashboardAggregationService, "get_open_positions", new_callable=AsyncMock
    ) as mock_positions:
        mock_positions.return_value = []
        return await service.get_risk_data(
            user, environment="paper", portfolio=portfolio, equity_curve=equity_curve
        )


@requires_risk_router
@pytest.mark.asyncio
async def test_risk_projection_reports_null_when_no_equity_series_was_gathered(
    mock_user, dashboard_service
):
    """An omitted or failed equity read yields `None`, never a zero standing in for one.

    `get_dashboard_data` sets `equity = []` when the equity gather raised, so this is the
    outage path as well as the never-passed one.
    """
    with patch.object(
        DashboardAggregationService, "get_open_positions", new_callable=AsyncMock
    ) as mock_positions:
        mock_positions.return_value = []

        omitted = await dashboard_service.get_risk_data(mock_user, environment="paper")
        failed_read = await dashboard_service.get_risk_data(
            mock_user, environment="paper", equity_curve=[]
        )

    assert omitted["current_drawdown_pct_v2"] is None
    assert failed_read["current_drawdown_pct_v2"] is None


# ══════════════════════════════════════════════════════════════════════════
# 4. THE TWO CLAIMS THAT MUST HOLD WHERE THE APP CANNOT BE BUILT
# ══════════════════════════════════════════════════════════════════════════
#
# Section 3 needs `backend_app.routers.risk` importable, and `routers/risk.py`'s handler needs
# the whole FastAPI app. Both are structural claims about source, and both are exactly the
# claims BC-1 is accountable for, so they are asserted from source here and hold on every run.


def test_the_deprecated_field_is_still_published_from_its_original_expression():
    """Requirement 19.1: BC-1 is additive. `current_drawdown_pct` must not have been repointed.

    Read from the bound method's source, so it is the definition that actually runs (this class
    declares `get_risk_data` twice and the later one wins).
    """
    source = inspect.getsource(DashboardAggregationService.get_risk_data)

    assert '"current_drawdown_pct":' in source, "the deprecated field was removed or renamed"
    assert '"current_drawdown_pct_v2":' in source, "the new field is absent"
    assert 'get("today_return_pct", 0.0)' in source, (
        "the deprecated field's expression changed; BC-1 must leave its value exactly as it was "
        "for the deprecation window"
    )
    assert "current_drawdown_pct_from_equity_curve(equity_curve)" in source, (
        "the new field must be the computed figure, not another literal"
    )


def test_risk_status_no_longer_returns_a_hardcoded_drawdown():
    """Requirement 19.2: `routers/risk.py::get_risk_status` must not fabricate `drawdown_pct`.

    The literal `0.0` claimed "no drawdown" about an account nothing had measured. Parsed from
    the file rather than imported, because the module needs a FastAPI whose `APIRouter` accepts
    `on_startup` and this assertion should not depend on that.
    """
    path = (
        Path(__file__).resolve().parent.parent
        / "backend_app" / "routers" / "risk.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    handler = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "get_risk_status"
        ),
        None,
    )
    assert handler is not None, "get_risk_status was renamed or removed"

    published = [
        value
        for node in ast.walk(handler)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "drawdown_pct"
    ]
    assert len(published) == 1, "expected exactly one drawdown_pct in the response body"

    assert not isinstance(published[0], ast.Constant), (
        "drawdown_pct is a hardcoded literal again; a fabricated number here tells a trader "
        "their account is at its peak when nothing measured it (Requirement 19.2)"
    )
    assert isinstance(published[0], ast.Name), (
        "drawdown_pct should be the value computed above the response body"
    )

    assert "current_drawdown_pct_from_equity_curve" in ast.dump(handler), (
        "the figure must come from the shared computation, so there is one definition of "
        "current drawdown in the codebase"
    )
