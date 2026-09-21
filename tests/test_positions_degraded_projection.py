"""
tests/test_positions_degraded_projection.py - vyomquant-ui-redesign BC-2 (task 12.2).

Requirements 14.5, 19.1, 19.2. `design.md` §1.6, §16.

WHAT THIS FILE EXISTS FOR
-------------------------
Two read paths swallowed failures into figures a trader could act on:

* `dashboard_aggregation_service.get_open_positions` caught a Redis failure and returned `[]`.
  An empty list is *also* what an account with no open positions returns, so an outage was
  indistinguishable from "you hold nothing". A trader reads the positions panel to decide
  whether they are exposed.
* `routers/dashboard.py::get_dashboard_overview` caught every exception and returned
  `total_value: 0.0, today_pnl: 0.0, …`. A broken read rendered as a zeroed portfolio.

BC-2's two dispositions, and which site takes which:

* `get_open_positions` raises `PositionsUnreadable`; `get_dashboard_data` catches it and
  publishes a top-level `degraded` block beside the balances, equity curve and executions that
  ARE still real reads. `positions` stays `[]` and `degraded` is what says why.
* `get_dashboard_overview` raises **503 DASHBOARD_FETCH_FAILED**, because all five figures it
  returns are headline money figures from one read. Nothing survives it, so there is no partial
  truth for a marker to qualify.

THE PAIR OF LOAD-BEARING TESTS HERE
    `test_an_unreadable_positions_read_is_never_an_empty_200` is the regression this task
    exists to prevent. Its mirror,
    `test_a_genuinely_empty_account_is_a_clean_200_with_no_marker`, is equally load-bearing:
    a fix that made "no positions" look like an outage would be the same defect reflected, and
    the frontend (task 13.1) renders `Panel state="error"` off this marker.
"""

import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from backend_app.backend import dashboard_aggregation_service as svc
from backend_app.backend.dashboard_aggregation_service import (
    POSITIONS_UNREADABLE,
    DashboardAggregationService,
    PositionsUnreadable,
    positions_degradation,
)


def _router_import_error():
    """Why `backend_app.routers.dashboard` cannot be imported here, or `None` if it can.

    Probed rather than assumed, exactly as `test_current_drawdown_projection.py` probes
    `backend_app.routers.risk`: `get_risk_data` imports the risk router inside its body, and
    section 4 needs the dashboard router. Where the app does not import, those tests say so
    instead of being reported as passing on a run that never executed them.
    """
    try:
        import backend_app.routers.dashboard  # noqa: F401
        import backend_app.routers.risk  # noqa: F401
    except Exception as exc:  # the reason is the useful part, so it is carried into the skip
        return f"{type(exc).__name__}: {exc}"
    return None


_ROUTER_IMPORT_ERROR = _router_import_error()

requires_routers = pytest.mark.skipif(
    _ROUTER_IMPORT_ERROR is not None,
    reason=(
        "backend_app.routers.dashboard / .risk are not importable in this environment: "
        + str(_ROUTER_IMPORT_ERROR)
    ),
)


@pytest.fixture
def mock_user():
    return {
        "id": "test_user_bc2_positions",
        "email": "trader@vyomquant.com",
        "access_token": "valid_jwt_token",
    }


@pytest.fixture
def dashboard_service():
    return DashboardAggregationService()


class _BrokenRedis(Exception):
    """A Redis outage, spelled as its own type so a test cannot mistake it for a bug in a test."""


def _redis_down():
    """Patch the Redis key scan `get_open_positions` starts its live read with, to raise.

    This is the real failure: `redis_manager.keys` is the first call inside the live branch's
    `try`, so raising here exercises the exact `except` BC-2 changed rather than a synthetic
    stand-in for it.
    """
    return patch(
        "backend_app.core.cache.redis_manager.redis_manager.keys",
        new_callable=AsyncMock,
        side_effect=_BrokenRedis("connection refused"),
    )


def _redis_empty():
    """Patch the same scan to succeed and find no position keys - a genuinely flat account."""
    return patch(
        "backend_app.core.cache.redis_manager.redis_manager.keys",
        new_callable=AsyncMock,
        return_value=[],
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE READ ITSELF  (Requirement 14.5)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_failed_live_positions_read_raises_instead_of_returning_empty(
    mock_user, dashboard_service
):
    """The defect, at its source: `get_open_positions` must not answer `[]` for a failure."""
    with _redis_down():
        with pytest.raises(PositionsUnreadable) as caught:
            await dashboard_service.get_open_positions(mock_user, environment="live")

    assert caught.value.environment == "live"
    # The cause is retained rather than flattened into a string, so a caller can classify it.
    assert isinstance(caught.value.cause, _BrokenRedis)
    assert "connection refused" in str(caught.value)


@pytest.mark.asyncio
async def test_a_failed_paper_positions_read_raises_too(mock_user, dashboard_service):
    """Both environments, or the paper dashboard keeps the defect BC-2 removed from live."""
    with patch(
        "backend_app.backend.paper_trading_service.get_paper_trading_service",
        side_effect=_BrokenRedis("paper store unavailable"),
    ):
        with pytest.raises(PositionsUnreadable) as caught:
            await dashboard_service.get_open_positions(mock_user, environment="paper")

    assert caught.value.environment == "paper"


@pytest.mark.asyncio
async def test_an_account_with_no_positions_still_returns_a_clean_empty_list(
    mock_user, dashboard_service
):
    """The mirror. `[]` must keep meaning "none open" - it is now the ONLY thing it means."""
    with _redis_empty():
        positions = await dashboard_service.get_open_positions(mock_user, environment="live")

    assert positions == []


# ══════════════════════════════════════════════════════════════════════════
# 2. THE MARKER'S SHAPE  (the `signal_trace` convention, reused not reinvented)
# ══════════════════════════════════════════════════════════════════════════


def test_the_degradation_block_is_none_when_nothing_is_degraded():
    """`None` in / `None` out, so one expression covers both cases at every call site."""
    assert positions_degradation(None, "live") is None


def test_the_degradation_block_names_what_is_unreadable_and_gives_a_renderable_reason():
    """Same shape as `signal_service`'s `degraded`: the named fact plus prose a page can show."""
    block = positions_degradation(_BrokenRedis("boom"), "live")

    assert block["positions"] == POSITIONS_UNREADABLE == "unreadable"
    assert block["environment"] == "live"
    assert isinstance(block["reason"], str) and block["reason"].strip()
    # The reason is for a trader, not a log reader: no exception text, no class name, no stack.
    assert "boom" not in block["reason"]
    assert "_BrokenRedis" not in block["reason"]


def test_the_marker_matches_the_established_signal_trace_degraded_convention():
    """BC-2 must not invent a second spelling of a convention this repo already has.

    `signal_service` publishes a top-level `degraded` that is `None` when healthy and a dict
    carrying a prose `reason` when not (`_lifecycle_degradation`, `_environment_degradation`).
    Asserted against that module directly, so the two cannot drift apart silently.
    """
    from backend_app.backend import signal_service

    established = signal_service._lifecycle_degradation(False)
    assert established is not None and signal_service._lifecycle_degradation(True) is None

    ours = positions_degradation(_BrokenRedis("x"), "live")
    assert isinstance(ours, dict) and isinstance(established, dict)
    assert "reason" in ours and "reason" in established, (
        "both must carry a renderable prose reason under the same key"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. THE DASHBOARD RESPONSE  (Requirement 14.5 - never an empty 200)
# ══════════════════════════════════════════════════════════════════════════


async def _dashboard(service, user, *, positions_broken: bool):
    """`get_dashboard_data` with only the positions read's fate varied.

    Every other gathered read is left to whatever it does in this environment - the point of
    BC-2 is that the rest of the response survives, so stubbing it out would test nothing.
    """
    with (_redis_down() if positions_broken else _redis_empty()):
        return await service.get_dashboard_data(user, equity_days=30, environment="live")


@requires_routers
@pytest.mark.asyncio
async def test_an_unreadable_positions_read_is_never_an_empty_200(mock_user, dashboard_service):
    """THE regression. An empty `positions[]` on a 200 must carry the marker that explains it.

    Requirement 14.5. This is the assertion task 13.1's `Panel state="error"` hangs off, and the
    reason it can never render an empty table during an outage.
    """
    data = await _dashboard(dashboard_service, mock_user, positions_broken=True)

    assert data["positions"] == []
    assert data["degraded"] is not None, (
        "an empty positions list on a 200 with no marker is the BC-2 defect: it asserts the "
        "account holds nothing, which this response cannot know"
    )
    assert data["degraded"]["positions"] == POSITIONS_UNREADABLE
    assert data["degraded"]["environment"] == "live"

    # The rest of the response is why this is a 200 at all rather than a 503.
    assert data["environment"] == "live"
    assert "overview" in data and "equity_curve" in data


@requires_routers
@pytest.mark.asyncio
async def test_a_genuinely_empty_account_is_a_clean_200_with_no_marker(
    mock_user, dashboard_service
):
    """The mirror, and equally load-bearing.

    A fix that made "no positions" look like an outage would be the same defect reflected: the
    page would show an error to every trader who is simply flat.
    """
    data = await _dashboard(dashboard_service, mock_user, positions_broken=False)

    assert data["positions"] == []
    assert data["degraded"] is None, "a flat account is not a degraded read"


@requires_routers
@pytest.mark.asyncio
async def test_the_risk_section_reports_null_rather_than_a_count_of_zero(
    mock_user, dashboard_service
):
    """`get_risk_data` is the other caller of `get_open_positions`, and it derives figures.

    Zero open positions and zero position utilisation are the SAFEST readings this projection
    can publish, which is exactly why publishing them unmeasured is the dangerous option -
    `risk_score` folds position utilisation in at 40% weight, so a swallowed failure reads as
    headroom.
    """
    broken = await _dashboard(dashboard_service, mock_user, positions_broken=True)
    healthy = await _dashboard(dashboard_service, mock_user, positions_broken=False)

    assert broken["risk"]["open_positions_count"] is None
    assert broken["risk"]["risk_score"] is None
    assert broken["risk"]["risk_level"] == "unavailable"
    assert broken["risk"]["degraded"]["positions"] == POSITIONS_UNREADABLE

    # And the healthy path is untouched: a real count, a real score, a real level.
    assert healthy["risk"]["open_positions_count"] == 0
    assert isinstance(healthy["risk"]["risk_score"], int)
    assert healthy["risk"]["risk_level"] in ("low", "medium", "high", "critical", "blocked")
    assert healthy["risk"]["degraded"] is None


@requires_routers
@pytest.mark.asyncio
async def test_the_risk_projection_reads_positions_once_when_handed_them(
    mock_user, dashboard_service
):
    """`get_dashboard_data` hands its gathered positions over rather than paying for a second
    read - and that is also what stops one response reporting them unreadable at the top level
    while publishing a count for them under `risk`."""
    with patch.object(
        DashboardAggregationService, "get_open_positions", new_callable=AsyncMock
    ) as mock_positions:
        mock_positions.return_value = []
        risk = await dashboard_service.get_risk_data(
            mock_user, environment="paper", positions=[]
        )

    assert mock_positions.await_count == 0, "a supplied empty list must not trigger a re-read"
    assert risk["open_positions_count"] == 0
    assert risk["degraded"] is None


@requires_routers
@pytest.mark.asyncio
async def test_get_risk_data_called_alone_still_degrades_rather_than_raising(
    mock_user, dashboard_service
):
    """Callers that do not pass `positions` (the tests in section 3 of BC-1's file, for one)
    must keep working: the method reads for itself and catches the same failure."""
    with _redis_down():
        risk = await dashboard_service.get_risk_data(mock_user, environment="live")

    assert risk["open_positions_count"] is None
    assert risk["risk_score"] is None
    assert risk["degraded"]["positions"] == POSITIONS_UNREADABLE


# ══════════════════════════════════════════════════════════════════════════
# 4. THE OVERVIEW ROUTE REFUSES  (Requirement 14.5 - no zeroed portfolio)
# ══════════════════════════════════════════════════════════════════════════


@requires_routers
def test_the_overview_endpoint_returns_503_instead_of_a_zeroed_portfolio(mock_user):
    """A trader with a broken read must not see `total_value: 0.0`.

    Driven through the real app so the assertion is about the STATUS CODE a client receives,
    not about a handler's return value in isolation.
    """
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "backend_app.routers.dashboard.get_dashboard_service", new_callable=AsyncMock
        ) as mock_get_service:
            service = AsyncMock()
            service.get_portfolio_overview = AsyncMock(
                side_effect=_BrokenRedis("portfolio read failed")
            )
            mock_get_service.return_value = service

            response = TestClient(app).get(
                "/api/dashboard/overview",
                headers={"Authorization": "Bearer valid_jwt_token"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["error"] == "DASHBOARD_FETCH_FAILED"
    # The zero-state body is gone, not merely accompanied by an error.
    assert "overview" not in body
    assert "0.0" not in json.dumps(body)


@requires_routers
def test_the_overview_endpoint_still_serves_a_real_portfolio(mock_user):
    """The mirror: a working read is a 200 with its figures, unchanged by BC-2."""
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "backend_app.routers.dashboard.get_dashboard_service", new_callable=AsyncMock
        ) as mock_get_service:
            service = AsyncMock()
            service.get_portfolio_overview = AsyncMock(
                return_value={
                    "total_equity": 50650.0,
                    "today_pnl": 150.0,
                    "pnl_pct": 0.3,
                    "unrealized_pnl": 500.0,
                    "available_balance": 40000.0,
                }
            )
            mock_get_service.return_value = service

            response = TestClient(app).get(
                "/api/dashboard/overview",
                headers={"Authorization": "Bearer valid_jwt_token"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["overview"]["total_value"] == 50650.0


# ══════════════════════════════════════════════════════════════════════════
# 5. THE CLAIMS THAT MUST HOLD WHERE THE APP CANNOT BE BUILT
# ══════════════════════════════════════════════════════════════════════════
#
# Section 3 and 4 need the FastAPI app. These two are structural claims about source, they are
# the two claims BC-2 is accountable for, and they hold on every run.


def test_the_positions_read_has_no_remaining_return_empty_on_failure():
    """Asserted from source: neither `except` in `get_open_positions` may answer with a list."""
    source = textwrap.dedent(inspect.getsource(DashboardAggregationService.get_open_positions))
    tree = ast.parse(source)

    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    assert len(handlers) == 2, "the paper and live read each have exactly one failure path"
    for handler in handlers:
        returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
        assert not returns, (
            "a failure path in get_open_positions returns a value again; BC-2's whole point is "
            "that a failed read cannot be spelled as a list (Requirement 14.5)"
        )
        raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
        assert raises, "a failure path in get_open_positions swallows silently"


def test_the_overview_handler_no_longer_returns_a_zero_state():
    """Asserted from source: `get_dashboard_overview`'s `except` must raise, not return zeros."""
    module = Path(svc.__file__).parent.parent / "routers" / "dashboard.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))

    handler_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "get_dashboard_overview"
    )

    for handler in [n for n in ast.walk(handler_node) if isinstance(n, ast.ExceptHandler)]:
        assert not [n for n in ast.walk(handler) if isinstance(n, ast.Return)], (
            "get_dashboard_overview returns a body from a failure path again; every figure it "
            "publishes is a headline money figure, so a zero there is a claim about the account"
        )

    body = ast.dump(handler_node)
    assert "DASHBOARD_FETCH_FAILED" in body, "the refusal must carry the documented error code"
