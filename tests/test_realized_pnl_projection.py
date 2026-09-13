"""
tests/test_realized_pnl_projection.py - vyomquant-ui-redesign BC-5 (task 12.5).

Requirements 10.1, 19.1, 19.2. `design.md` §7.6, §16.

WHAT THIS FILE EXISTS FOR
-------------------------
Requirement 10.1 asks the Portfolio page for realised P&L. The overview projection published two
figures that look like it and neither was it:

* `today_realized_pnl` IS realised, but only since 00:00 UTC - not a lifetime figure.
* `cumulative_pnl` IS lifetime, but it is TOTAL P&L: realised plus the mark-to-market on
  positions still open. Rendering it as "realised" would report unbanked money as banked.

So the page had no source and rendered not-available. BC-5 adds the missing figure as the same
`executions.pnl` sum `today_realized_pnl` already comes from, with the day filter removed - one
expression, one definition of "realised", and one round trip for both windows.

THE TWO LOAD-BEARING PAIRS HERE
    1. `test_live_lifetime_realized_pnl_differs_from_today_and_both_are_present` and its paper
       twin: the spec's named verification. A "lifetime" figure that equals today's would mean
       the day filter is still on, and one that replaced today's would break every existing
       consumer of `today_realized_pnl` (Requirement 19.1).
    2. `test_an_unreadable_executions_read_reports_null_rather_than_zero` and
       `test_a_ledger_that_nets_exactly_zero_reports_zero_rather_than_null`. Zero is what an
       account whose closed trades cancel out has genuinely realised, so it cannot also be the
       spelling of "we could not read it" - a trader reading `0.00` during an outage would
       believe they had banked nothing. This is BC-1's and BC-2's rule, applied to a third field
       rather than restated as a third convention.
"""

import re

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from backend_app.backend.dashboard_aggregation_service import (
    EXECUTION_COUNT_COLUMN,
    REALIZED_PNL_COLUMN,
    TODAY_REALIZED_PNL_COLUMN,
    DashboardAggregationService,
    executions_realized_pnl_query,
    realized_pnl_from_execution_totals,
)
from backend_app.backend.paper_trading_service import get_paper_trading_service
from tests.paper_seed import bind_paper_persistence, release_paper_persistence, seed_fill


def _router_import_error():
    """Why `backend_app.routers.risk` cannot be imported here, or `None` if it can.

    Probed rather than assumed, as `test_positions_degraded_projection.py` and
    `test_current_drawdown_projection.py` both do: `get_risk_data` imports the risk router
    inside its body, so the `get_dashboard_data` sections below need it. Where the app does not
    import, those tests say so instead of being reported as passing on a run that skipped them.
    """
    try:
        import backend_app.routers.risk  # noqa: F401
    except Exception as exc:  # the reason is the useful part, so it is carried into the skip
        return f"{type(exc).__name__}: {exc}"
    return None


_ROUTER_IMPORT_ERROR = _router_import_error()

requires_routers = pytest.mark.skipif(
    _ROUTER_IMPORT_ERROR is not None,
    reason="backend_app.routers.risk is not importable here: " + str(_ROUTER_IMPORT_ERROR),
)


@pytest.fixture
def mock_user():
    return {
        "id": "test_user_bc5_realized",
        "email": "trader@vyomquant.com",
        "access_token": "valid_jwt_token",
    }


@pytest.fixture
def dashboard_service():
    return DashboardAggregationService()


@pytest.fixture
def paper_persistence():
    """The storage the paper reads require, as `test_dashboard_phase1_contract.py` binds it."""
    service = get_paper_trading_service()
    bind_paper_persistence(service)
    try:
        yield service
    finally:
        release_paper_persistence(service)


def _iso(days_ago: int = 0, hour: int = 12) -> str:
    day = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


class _BrokenQuestDB(Exception):
    """A telemetry outage, spelled as its own type so a test cannot mistake it for a test bug."""


class _FakeQuestDB:
    """A telemetry double that ANSWERS the query it is handed, rather than a fixed figure.

    It parses the day cutoff out of `executions_realized_pnl_query` and aggregates a fixture
    ledger with SQL's own null semantics - `sum` skips nulls, and a `sum` over no rows is NULL -
    so what these tests exercise is the service's reading of a QuestDB-shaped response, not a
    pre-baked answer that would pass whatever the service did with it.

    `queries` records every statement issued, which is how the one-round-trip claim is asserted.
    """

    def __init__(self, ledger=None, *, fail=False):
        #: `(iso_timestamp, pnl)` per execution row. `pnl` may be `None` - QuestDB's is nullable.
        self.ledger = list(ledger or [])
        self.fail = fail
        self.queries = []

    async def execute_query(self, query: str):
        self.queries.append(query)
        if self.fail:
            raise _BrokenQuestDB("connection refused")
        if "live_user_pnl" in query:
            return {
                "columns": [
                    {"name": "total_equity"},
                    {"name": "total_pnl"},
                    {"name": "pnl_pct"},
                    {"name": "total_exposure"},
                ],
                "dataset": [[10_000.0, 250.0, 2.5, 0.0]],
            }
        if "FROM executions" not in query:
            return {"columns": [], "dataset": []}

        cutoff_date = re.search(r"to_timestamp\('(\d{4}-\d{2}-\d{2})T", query).group(1)
        cutoff = f"{cutoff_date}T00:00:00"
        if not self.ledger:
            today_cell = None  # sum over zero rows is NULL
            lifetime_cell = None
        else:
            today_cell = float(
                sum(
                    (pnl if (pnl is not None and ts >= cutoff) else 0.0)
                    for ts, pnl in self.ledger
                )
            )
            reported = [pnl for _, pnl in self.ledger if pnl is not None]
            lifetime_cell = float(sum(reported)) if reported else None
        return {
            "columns": [
                {"name": TODAY_REALIZED_PNL_COLUMN},
                {"name": REALIZED_PNL_COLUMN},
                {"name": EXECUTION_COUNT_COLUMN},
            ],
            "dataset": [[today_cell, lifetime_cell, len(self.ledger)]],
        }

    def executions_queries(self):
        return [q for q in self.queries if "FROM executions" in q]


def _live(questdb):
    """The two patches every live read here needs: `questdb` as telemetry, and no cached balances.

    Redis is patched to find no keys - a real read that finds nothing - so the balance figures
    take their derived path and the only thing varying between these tests is the ledger.
    """
    return patch.object(
        DashboardAggregationService, "_get_telemetry", return_value=questdb
    ), patch(
        "backend_app.core.cache.redis_manager.redis_manager.keys",
        new_callable=AsyncMock,
        return_value=[],
    )


async def _overview(service, user, questdb):
    telemetry_patch, redis_patch = _live(questdb)
    with telemetry_patch, redis_patch:
        return await service.get_portfolio_overview(user, environment="live")


async def _dashboard(service, user, questdb):
    telemetry_patch, redis_patch = _live(questdb)
    with telemetry_patch, redis_patch:
        return await service.get_dashboard_data(user, equity_days=30, environment="live")


# ══════════════════════════════════════════════════════════════════════════
# 1. ONE READ, ONE DEFINITION OF "REALISED"
# ══════════════════════════════════════════════════════════════════════════


def test_both_figures_are_selected_from_one_read_of_the_same_pnl_column():
    """The lifetime sum must be the day-filtered sum minus its window, not a second definition."""
    query = executions_realized_pnl_query("user_abc", "2024-05-17")

    assert query.count("FROM executions") == 1, "both figures come off one scan of one table"
    assert f"AS {TODAY_REALIZED_PNL_COLUMN}" in query
    assert f"AS {REALIZED_PNL_COLUMN}" in query
    # The lifetime figure is a bare sum of the same column; the day window is a condition inside
    # the aggregate rather than a predicate in the WHERE, which is what lets one read serve both.
    assert "sum(pnl)" in query
    assert "then pnl" in query
    where_clause = query.split("WHERE", 1)[1]
    assert "to_timestamp" not in where_clause, (
        "the day cutoff is back in the WHERE clause; the lifetime sum would then be filtered too"
    )
    assert "user_id = 'user_abc'" in where_clause


@pytest.mark.asyncio
async def test_the_live_overview_issues_no_second_executions_query(mock_user, dashboard_service):
    """BC-5 adds a figure, not a round trip."""
    questdb = _FakeQuestDB([(_iso(0), 25.0)])

    await _overview(dashboard_service, mock_user, questdb)

    assert len(questdb.executions_queries()) == 1, (
        "the lifetime figure must come off the read that already produced today's, not a second"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. BOTH FIGURES, AND THEY DIFFER  (the spec's named verification, Requirement 10.1)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_lifetime_realized_pnl_differs_from_today_and_both_are_present(
    mock_user, dashboard_service
):
    """Executions on two different days: today's window is a subset, so the figures differ."""
    questdb = _FakeQuestDB(
        [
            (_iso(3), 500.0),   # banked three days ago
            (_iso(1), -125.0),  # banked yesterday
            (_iso(0), 150.0),   # banked today
        ]
    )

    overview = await _overview(dashboard_service, mock_user, questdb)

    assert "today_realized_pnl" in overview and "realized_pnl" in overview, (
        "both must be present - the page needs today's scoped figure AND the lifetime one"
    )
    assert overview["today_realized_pnl"] == 150.0
    assert overview["realized_pnl"] == 525.0  # 500 - 125 + 150
    assert overview["realized_pnl"] != overview["today_realized_pnl"], (
        "a lifetime figure equal to today's means the day filter is still applied to it"
    )


@pytest.mark.asyncio
async def test_paper_lifetime_realized_pnl_differs_from_today_and_both_are_present(
    mock_user, dashboard_service, paper_persistence
):
    """The same claim over the real paper fill ledger - no query double anywhere in this one."""
    paper_svc = paper_persistence
    paper_svc.reset_account(mock_user["id"], capital=50_000.0)

    seed_fill(
        paper_svc,
        mock_user["id"],
        symbol="BTC/USDT",
        side="sell",
        quantity="0.1",
        price="60000.0",
        realized_pnl="500.00",
        executed_at=_iso(1),
        execution_id="exec_bc5_yesterday",
    )
    seed_fill(
        paper_svc,
        mock_user["id"],
        symbol="ETH/USDT",
        side="sell",
        quantity="1.0",
        price="3500.0",
        realized_pnl="150.00",
        executed_at=_iso(0),
        execution_id="exec_bc5_today",
    )

    overview = await dashboard_service.get_portfolio_overview(mock_user, environment="paper")

    assert overview["today_realized_pnl"] == 150.00
    assert overview["realized_pnl"] == 650.00  # 500 yesterday + 150 today
    assert overview["realized_pnl"] != overview["today_realized_pnl"]


@pytest.mark.asyncio
async def test_lifetime_realized_pnl_is_not_cumulative_pnl(mock_user, dashboard_service):
    """The other half of the gap: `cumulative_pnl` includes unrealised, so it is not realised.

    Requirement 10.1 asks for money BANKED. With an open position marked up, the two figures
    must come apart - if they did not, `cumulative_pnl` would have been an honest source all
    along and BC-5 would be redundant.
    """
    questdb = _FakeQuestDB([(_iso(2), 400.0)])

    overview = await _overview(dashboard_service, mock_user, questdb)

    assert overview["realized_pnl"] == 400.0
    # `cumulative_pnl` is `live_user_pnl.total_pnl` - total P&L, realised plus open marks.
    assert overview["cumulative_pnl"] == 250.0
    assert overview["realized_pnl"] != overview["cumulative_pnl"]


# ══════════════════════════════════════════════════════════════════════════
# 3. UNREADABLE IS NULL; A REAL ZERO IS ZERO  (Requirement 19.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unreadable_executions_read_reports_null_rather_than_zero(
    mock_user, dashboard_service
):
    """THE honesty regression. `0.0` here would tell a trader they have banked nothing."""
    overview = await _overview(dashboard_service, mock_user, _FakeQuestDB(fail=True))

    assert overview["realized_pnl"] is None, (
        "the read failed, so there is no lifetime realised figure; 0.0 would assert that the "
        "account has banked nothing, which this response cannot know (Requirement 19.2)"
    )

    # And the additive guarantee (Requirement 19.1): the field that has always published 0.0 for
    # this failure still does. BC-5 does not hand an existing consumer a null it never saw.
    assert overview["today_realized_pnl"] == 0.0
    assert isinstance(overview["today_realized_pnl"], float)


@pytest.mark.asyncio
async def test_a_ledger_that_nets_exactly_zero_reports_zero_rather_than_null(
    mock_user, dashboard_service
):
    """The mirror, and equally load-bearing: a real zero is a fact and must survive as one."""
    questdb = _FakeQuestDB([(_iso(4), 300.0), (_iso(2), -300.0)])

    overview = await _overview(dashboard_service, mock_user, questdb)

    assert overview["realized_pnl"] == 0.0
    assert overview["realized_pnl"] is not None, (
        "an account whose closed trades cancel out has realised exactly zero; reporting that as "
        "not-available would hide a fact the trader is entitled to read"
    )


@pytest.mark.asyncio
async def test_a_paper_ledger_that_nets_exactly_zero_reports_zero_rather_than_null(
    mock_user, dashboard_service, paper_persistence
):
    """The same distinction on the paper side, over real fills."""
    paper_svc = paper_persistence
    paper_svc.reset_account(mock_user["id"], capital=50_000.0)

    seed_fill(
        paper_svc,
        mock_user["id"],
        symbol="BTC/USDT",
        side="sell",
        quantity="0.1",
        price="60000.0",
        realized_pnl="75.00",
        executed_at=_iso(2),
        execution_id="exec_bc5_win",
    )
    seed_fill(
        paper_svc,
        mock_user["id"],
        symbol="BTC/USDT",
        side="sell",
        quantity="0.1",
        price="59000.0",
        realized_pnl="-75.00",
        executed_at=_iso(1),
        execution_id="exec_bc5_loss",
    )

    overview = await dashboard_service.get_portfolio_overview(mock_user, environment="paper")

    assert overview["realized_pnl"] == 0.0
    assert overview["realized_pnl"] is not None


def test_a_null_sum_over_a_ledger_that_has_rows_is_unreadable():
    """Rows exist but `pnl` is not reporting for them, so no sum of it can be published."""
    _, lifetime = realized_pnl_from_execution_totals(
        {
            "columns": [
                {"name": TODAY_REALIZED_PNL_COLUMN},
                {"name": REALIZED_PNL_COLUMN},
                {"name": EXECUTION_COUNT_COLUMN},
            ],
            "dataset": [[None, None, 7]],
        }
    )

    assert lifetime is None


def test_a_null_sum_over_an_empty_ledger_is_a_real_zero():
    """An account that has never executed has realised nothing, and the read said so."""
    _, lifetime = realized_pnl_from_execution_totals(
        {
            "columns": [
                {"name": TODAY_REALIZED_PNL_COLUMN},
                {"name": REALIZED_PNL_COLUMN},
                {"name": EXECUTION_COUNT_COLUMN},
            ],
            "dataset": [[None, None, 0]],
        }
    )

    assert lifetime == 0.0


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"dataset": []},
        {"dataset": [[]]},
        # A response that does not name the column: its shape is being guessed at, and a guessed
        # money figure is the fabrication Requirement 19.2 forbids.
        {"dataset": [[12.5, 99.0, 3]]},
    ],
)
def test_a_response_the_figure_cannot_be_read_from_yields_null(result):
    _, lifetime = realized_pnl_from_execution_totals(result)

    assert lifetime is None


def test_todays_figure_keeps_its_historical_zero_and_its_positional_read():
    """`today_realized_pnl` is not the field that gains a null (Requirement 19.1).

    Its second claim: an unnamed response is still read at index 0, which is the exact cell the
    single-figure query BC-5 replaced was read from (`pnl_res["dataset"][0][0]`).
    """
    assert realized_pnl_from_execution_totals(None)[0] == 0.0
    assert realized_pnl_from_execution_totals({"dataset": [[None, None, 4]]})[0] == 0.0
    assert realized_pnl_from_execution_totals({"dataset": [[12.5, 99.0, 3]]})[0] == 12.5


# ══════════════════════════════════════════════════════════════════════════
# 4. ADDITIVE, AND CARRIED ONTO THE DASHBOARD RESPONSE  (Requirement 19.1)
# ══════════════════════════════════════════════════════════════════════════

#: Every key `get_portfolio_overview` published before BC-5, in both environments. Spelled out so
#: the test below fails if BC-5 renamed, dropped or shadowed one of them rather than adding to
#: them - `realized_pnl` in particular must be a NEW key, not a repointing of an existing figure.
_KEYS_BEFORE_BC5 = frozenset(
    {
        "total_equity",
        "total_value",
        "available_balance",
        "free_balance",
        "used_balance",
        "today_pnl",
        "today_realized_pnl",
        "today_return_pct",
        "unrealized_pnl",
        "cumulative_pnl",
        "total_exposure",
        "currency",
        "environment",
        "updated_at",
    }
)


@pytest.mark.asyncio
async def test_the_overview_gains_exactly_one_key_and_loses_none(mock_user, dashboard_service):
    """`realized_pnl` was free to take: it is added beside the old keys, shadowing nothing."""
    live = await _overview(dashboard_service, mock_user, _FakeQuestDB([(_iso(0), 10.0)]))

    assert "realized_pnl" not in _KEYS_BEFORE_BC5, "the name would collide with an existing field"
    assert set(live) == _KEYS_BEFORE_BC5 | {"realized_pnl"}


@requires_routers
@pytest.mark.asyncio
async def test_the_dashboard_overview_publishes_the_lifetime_figure_beside_todays(
    mock_user, dashboard_service
):
    """`GET /api/dashboard` rebuilds `overview` key by key, so the field has to be added there
    too or the page that consumes it (task 16.1) never sees it."""
    questdb = _FakeQuestDB([(_iso(2), 800.0), (_iso(0), 60.0)])

    data = await _dashboard(dashboard_service, mock_user, questdb)

    assert data["overview"]["today_realized_pnl"] == 60.0
    assert data["overview"]["realized_pnl"] == 860.0


@requires_routers
@pytest.mark.asyncio
async def test_the_dashboard_overview_carries_the_null_through_rather_than_flooring_it(
    mock_user, dashboard_service
):
    """The `float()` its neighbours get would turn the honest null into the lie it replaces."""
    data = await _dashboard(dashboard_service, mock_user, _FakeQuestDB(fail=True))

    assert "realized_pnl" in data["overview"], "absent is not the same statement as null"
    assert data["overview"]["realized_pnl"] is None
    # Unchanged neighbours, for the same additive reason as above.
    assert data["overview"]["today_realized_pnl"] == 0.0
    assert data["overview"]["cumulative_pnl"] == 0.0
