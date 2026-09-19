"""
tests/test_dashboard_absent_figures.py - production-launch-hardening task 1, CLUSTER A.

Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.36. `design.md` §Hypothesized Root Cause (wave 1).

WHAT THIS FILE IS
-----------------
**Bug condition exploration tests.** Every test in sections 1-4 is EXPECTED TO FAIL against the
current tree (`F`). The failure is the deliverable: it is the counterexample that proves the
defect exists, and the same assertion is what validates the fix in task 11.1. Nothing here is a
symptom patch and no assertion has been weakened to make a run green.

THE DEFECT, IN ONE SENTENCE
---------------------------
`dashboard_aggregation_service` answers "we could not read this" with a *number*. A trader cannot
tell a fabricated figure from a measured one, and `100000.0` - the paper starting capital - is the
number it picks.

The four sites, with the value each one invents:

| # | Site | On a failed / absent read, `F` returns |
|---|---|---|
| 1 | `get_portfolio_overview` paper `except` (`:958-961`) | `total_equity`, `total_value`, `available_balance`, `free_balance` = **`100000.0`** |
| 2 | `get_dashboard_data` composer fallback (`:1692-1695`) | **`100000.0`** in paper, **`0.0`** in live |
| 3 | `get_equity_curve` paper synthesis (`:1338-1344`) | a **two-point flat curve at `100000.0`** spanning the requested window |
| 4 | `get_portfolio_overview` paper row reads (`:897-903`) | `float(acct.get("total_equity", 100000.0))` - the **default wins over the absent column** |

Site 2's live figure is the worse of the two. `100000.0` is at least implausible enough for a
trader to question; `0.0` is a number a real account can genuinely hold, so an outage renders as
a wiped-out portfolio and is indistinguishable from one.

THE HONEST MODEL ALREADY IN THIS FILE
-------------------------------------
`get_health_status` (`:1456-1486`) reports `exchange_api_latency_ms: None` and
`exchange_api_latency_status: "unavailable"` when nothing was measured, and only classifies once
it has a figure. `get_risk_data`'s `current_drawdown_pct_v2` and the overview's `realized_pnl`
(BC-1, BC-5) do the same. The fixed-state expectations below are that convention applied to the
four money figures, not a new one invented here.

THE REFUTATION PROBE (section 5)
--------------------------------
`design.md` hypothesises that the composer's `float(portfolio.get(key, 0.0))` is the ONLY gate
forcing a number, so retyping it is the whole of wave 1 step 1. Section 5 feeds a `None`-bearing
portfolio through `get_dashboard_data` and reports which way it went. If the `None` reaches the
response without a `TypeError`, the hypothesis is refuted and wave 1 is larger than designed.

HARNESS
-------
Reused from `tests/test_positions_degraded_projection.py` and
`tests/test_realized_pnl_projection.py`, which already exercise this service: `_get_telemetry`
patched with a QuestDB double, `redis_manager.keys` patched to find nothing, the paper singleton
bound to `tests/paper_seed`'s Persistence_Layer double, and the risk-router import probed rather
than assumed. No new harness is stood up.
"""

import json
import traceback

import pytest
from unittest.mock import AsyncMock, patch

from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService
from backend_app.backend.paper_trading_service import get_paper_trading_service
from tests.paper_seed import bind_paper_persistence, release_paper_persistence


#: The paper starting capital, and the figure every site below reaches for when it has nothing.
#: Named once so a test asserts against the fabrication rather than against a bare literal.
FABRICATED_CAPITAL = 100000.0

#: The four figures `design.md` §1.1 names. Absent means absent for all four or for none: a
#: response that nulls `total_equity` while still publishing `total_value` is the same defect with
#: one fewer symptom.
MONEY_KEYS = ("total_equity", "total_value", "available_balance", "free_balance")

#: The same four as the composer spells them in `overview`.
OVERVIEW_MONEY_KEYS = ("total_equity", "total_value", "available_balance", "free_balance")


def _router_import_error():
    """Why `backend_app.routers.risk` cannot be imported here, or `None` if it can.

    Probed exactly as `test_realized_pnl_projection.py` probes it: `get_risk_data` and
    `get_health_status` both import the risk router inside their bodies, so the
    `get_dashboard_data` sections need it. Where the app does not import, those tests say so
    instead of being reported as passing on a run that never executed them.
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
        "id": "test_user_wave1_absent",
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


class _PaperStoreDown(Exception):
    """A paper-store outage, spelled as its own type so a test cannot mistake it for a test bug."""


class _EmptyQuestDB:
    """Telemetry that answers every query with a real, successful, EMPTY result set.

    This is the premise of section 3: QuestDB was reachable and simply holds no equity rows for
    this user yet. Not an error - an honest empty read, which is precisely the case `F` decides
    to fill in with a curve it invented.
    """

    def __init__(self):
        self.queries = []

    async def execute_query(self, query: str):
        self.queries.append(query)
        return {"columns": [], "dataset": []}


def _paper_service_raising():
    """Patch the paper service factory to raise, driving `get_portfolio_overview` into its `except`.

    `get_paper_trading_service` is imported inside the paper branch's `try`, so raising at the
    factory exercises the exact `except Exception as paper_err` at `:957` rather than a synthetic
    stand-in for it. Same patch target `test_positions_degraded_projection.py` uses.
    """
    return patch(
        "backend_app.backend.paper_trading_service.get_paper_trading_service",
        side_effect=_PaperStoreDown("paper store unavailable"),
    )


#: A complete, healthy paper account row. Every figure here is a real persisted value; section 4
#: deletes exactly one key from a copy of it. `unrealized_pnl` is deliberately non-zero so
#: `today_pnl` is non-zero and `today_return_pct` is a percentage that had to be divided by
#: SOMETHING - when `initial_capital` is the omitted column, that something is the fabrication.
COMPLETE_PAPER_ROW = {
    "account_id": "acct_paper_stub",
    "total_equity": 4200.0,
    "available_balance": 4200.0,
    "locked_balance": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 500.0,
    "initial_capital": 4000.0,
}


def _paper_account_row(row):
    """A stub paper service whose account row is exactly `row` - absent keys genuinely absent.

    A `None` value would still be *present*, and `.get(key, default)` only reaches its default
    for a missing key, so section 4 has to delete rather than null. `get_trades` answers with an
    empty ledger: the subject of those tests is the absent column, not the fill history.
    """

    class _StubPaperService:
        def get_or_create_account(self, user_id):
            return dict(row)

        def get_trades(self, user_id):
            return []

    return patch(
        "backend_app.backend.paper_trading_service.get_paper_trading_service",
        return_value=_StubPaperService(),
    )


def _no_cached_balances():
    """Redis patched to find no keys - a real read that finds nothing, as `_live` does."""
    return patch(
        "backend_app.core.cache.redis_manager.redis_manager.keys",
        new_callable=AsyncMock,
        return_value=[],
    )


def _fabricated_capital_sites(payload):
    """Every path inside `payload` whose value is numerically `100000.0`.

    Recursive and by *value*, not by `json.dumps` substring match, so a legitimate `100000` inside
    a timestamp or an id cannot be mistaken for the fabrication and a `Decimal("100000.0")` cannot
    hide from it.
    """
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            if float(node) == FABRICATED_CAPITAL:
                found.append(path)

    walk(payload, "$")
    return found


def _money_block(overview):
    """The four figures under test, as a comparable dict."""
    return {key: overview.get(key) for key in OVERVIEW_MONEY_KEYS}


def _deepest_service_frame(exc):
    """`file:line` of the deepest frame inside the service for `exc`, for the section 5 report."""
    frames = [
        frame
        for frame in traceback.extract_tb(exc.__traceback__)
        if "dashboard_aggregation_service" in frame.filename
    ]
    if not frames:
        return "<no frame inside dashboard_aggregation_service>"
    last = frames[-1]
    return f"dashboard_aggregation_service.py:{last.lineno}  ->  {(last.line or '').strip()}"


# ══════════════════════════════════════════════════════════════════════════
# 1. THE PAPER READ FAILED  (`get_portfolio_overview` :958-961, Requirements 1.1, 1.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_failed_paper_portfolio_read_reports_absent_not_the_starting_capital(
    mock_user, dashboard_service
):
    """The paper store is unreachable, so there is no balance. `None`, not `100000.0`.

    COUNTEREXAMPLE OBSERVED ON `F` (dashboard_aggregation_service.py:958-961)::

        {"total_equity": 100000.0, "total_value": 100000.0,
         "available_balance": 100000.0, "free_balance": 100000.0, ...}

    Four headline money figures, none of them read from anything. The `except` block hands back
    the paper *starting* capital, so a trader whose account has been running for a month and has
    just lost the store sees the balance they opened with.
    """
    with _paper_service_raising():
        overview = await dashboard_service.get_portfolio_overview(mock_user, environment="paper")

    for key in MONEY_KEYS:
        assert overview[key] is None, (
            f"{key} was not read, so it must be None. "
            f"`F` returns {overview[key]!r} here (:958-961)."
        )


@pytest.mark.asyncio
async def test_the_failed_paper_response_carries_the_fabricated_capital_nowhere(
    mock_user, dashboard_service
):
    """The whole response, not just the four named keys - the same default is reached for twice.

    COUNTEREXAMPLE OBSERVED ON `F`: `100000.0` appears at four paths -
    `$.total_equity`, `$.total_value`, `$.available_balance`, `$.free_balance`.

    Asserted over the entire payload rather than key by key because the defect is the *literal*,
    and a fix that nulled the four named keys while leaving the same number somewhere else in the
    body (`initial_capital`, an equity point, a derived exposure) would still publish it.
    """
    with _paper_service_raising():
        overview = await dashboard_service.get_portfolio_overview(mock_user, environment="paper")

    sites = _fabricated_capital_sites(overview)
    assert sites == [], (
        f"the paper starting capital {FABRICATED_CAPITAL} was synthesised at {sites} on a read "
        f"that failed; nothing in this response was read from the paper store"
    )


@pytest.mark.asyncio
async def test_the_failed_paper_response_still_says_which_environment_it_failed_in(
    mock_user, dashboard_service
):
    """Preservation inside an exploration file: nulling the figures must not null the envelope.

    `environment`, `currency` and `updated_at` are facts about the REQUEST, not about the read, so
    they survive. This passes on `F` and must keep passing on `F'` - it is the guard against
    "fix" by returning an empty dict.
    """
    with _paper_service_raising():
        overview = await dashboard_service.get_portfolio_overview(mock_user, environment="paper")

    assert overview["environment"] == "paper"
    assert overview["currency"] == "USD"
    assert overview["updated_at"], "the response is still timestamped"


# ══════════════════════════════════════════════════════════════════════════
# 2. THE COMPOSER'S FALLBACK  (`get_dashboard_data` :1692-1695, Requirements 1.3, 1.4)
# ══════════════════════════════════════════════════════════════════════════


def _portfolio_overview_raising():
    """Patch `get_portfolio_overview` itself to raise, which is the ONLY way to reach :1692.

    The paper branch catches its own failure (section 1), so a broken paper store never surfaces
    as an `Exception` in `asyncio.gather`'s results. The composer's fallback is reached when the
    method raises outright - a `_safe_uid` refusal, a telemetry import failure, a cancellation -
    and that is what is simulated here.
    """
    return patch.object(
        DashboardAggregationService,
        "get_portfolio_overview",
        new_callable=AsyncMock,
        side_effect=_PaperStoreDown("portfolio read raised"),
    )


def _portfolio_overview_returning(payload):
    """Patch `get_portfolio_overview` to SUCCEED and return `payload`."""
    return patch.object(
        DashboardAggregationService,
        "get_portfolio_overview",
        new_callable=AsyncMock,
        return_value=payload,
    )


def _genuine_zero_portfolio(environment):
    """A portfolio that was read successfully and is genuinely, truthfully, empty.

    A funded account withdrawn to zero. Every figure here is a real measurement whose value
    happens to be `0.0`, and it must stay `0.0` end to end (`tasks.md`: *a genuine `0.0` stays
    `0.0`*). This is the control for `test_a_failed_read_is_distinguishable_from_a_real_zero`.
    """
    return {
        "total_equity": 0.0,
        "total_value": 0.0,
        "available_balance": 0.0,
        "free_balance": 0.0,
        "used_balance": 0.0,
        "today_pnl": 0.0,
        "today_realized_pnl": 0.0,
        "realized_pnl": 0.0,
        "today_return_pct": 0.0,
        "unrealized_pnl": 0.0,
        "cumulative_pnl": 0.0,
        "total_exposure": 0.0,
        "currency": "USD" if environment == "paper" else "USDT",
        "environment": environment,
        "updated_at": "2024-05-17T00:00:00+00:00",
    }


async def _dashboard_with_portfolio_failure(service, user, environment):
    with _portfolio_overview_raising(), _no_cached_balances(), patch.object(
        DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
    ):
        return await service.get_dashboard_data(user, equity_days=30, environment=environment)


@requires_routers
@pytest.mark.parametrize(
    "environment, fabrication_on_F",
    [
        # :1693 - `100000.0 if norm_env == "paper" else 0.0`, one expression, two fabrications.
        ("paper", 100000.0),
        ("live", 0.0),
    ],
)
@pytest.mark.asyncio
async def test_the_composer_reports_absent_in_both_environments(
    mock_user, dashboard_service, environment, fabrication_on_F
):
    """A portfolio read that raised has no figures. Both environments, same answer: `None`.

    COUNTEREXAMPLE OBSERVED ON `F` (dashboard_aggregation_service.py:1692-1695)::

        paper -> {"total_equity": 100000.0, "total_value": 100000.0,
                  "available_balance": 100000.0, "free_balance": 100000.0}
        live  -> {"total_equity": 0.0, "total_value": 0.0,
                  "available_balance": 0.0, "free_balance": 0.0}

    Both environments are asserted in one parametrised test because `F` fabricates a DIFFERENT
    number in each (`100000.0 if norm_env == "paper" else 0.0`, :1693). Fixing only the paper
    limb would leave the live limb publishing `0.0`, which is the more dangerous of the two - a
    real account can genuinely hold zero, so there is nothing implausible for a trader to notice.
    """
    data = await _dashboard_with_portfolio_failure(dashboard_service, mock_user, environment)

    overview = data["overview"]
    for key in OVERVIEW_MONEY_KEYS:
        assert overview[key] is None, (
            f"{environment}: {key} came from a read that raised, so it must be None. "
            f"`F` returns {overview[key]!r} (fabricated as {fabrication_on_F} at :1692-1695, then "
            f"re-coerced by float() at the composer's overview block)."
        )


@requires_routers
@pytest.mark.parametrize("environment", ["paper", "live"])
@pytest.mark.asyncio
async def test_a_failed_read_is_distinguishable_from_a_real_zero(
    mock_user, dashboard_service, environment
):
    """The load-bearing one. An outage and an empty account must not produce the same numbers.

    COUNTEREXAMPLE OBSERVED ON `F`, live: the two responses are byte-identical across all four
    figures - `{"total_equity": 0.0, "total_value": 0.0, "available_balance": 0.0,
    "free_balance": 0.0}` either way. No consumer, frontend or otherwise, can tell them apart,
    which is what makes this a correctness defect rather than a cosmetic one.

    In paper `F` happens to differ (`100000.0` vs `0.0`) - but it differs by substituting one
    fabrication for another, so the paper limb is not "already correct"; it is wrong in a way this
    particular comparison cannot see. Section 2's first test is what pins paper.
    """
    failed = await _dashboard_with_portfolio_failure(dashboard_service, mock_user, environment)

    with _portfolio_overview_returning(
        _genuine_zero_portfolio(environment)
    ), _no_cached_balances(), patch.object(
        DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
    ):
        real_zero = await dashboard_service.get_dashboard_data(
            mock_user, equity_days=30, environment=environment
        )

    # The control half: a genuine zero survives unchanged. This must hold on `F` and on `F'`.
    assert _money_block(real_zero["overview"]) == dict.fromkeys(OVERVIEW_MONEY_KEYS, 0.0), (
        "a successful read of an empty account must report 0.0, not None - collapsing the two is "
        "wave 1's own failure mode"
    )

    assert _money_block(failed["overview"]) != _money_block(real_zero["overview"]), (
        f"{environment}: a portfolio read that RAISED produced "
        f"{_money_block(failed['overview'])}, which is numerically identical to a genuine "
        f"zero-balance account. An outage is being rendered as a wiped-out portfolio."
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. NO EQUITY ROWS  (`get_equity_curve` :1338-1344, Requirements 1.5, 1.36)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_paper_equity_curve_with_no_rows_is_empty_not_a_synthesised_flat_line(
    mock_user, dashboard_service
):
    """QuestDB answered, and it holds no equity rows. An empty series, not an invented one.

    COUNTEREXAMPLE OBSERVED ON `F` (dashboard_aggregation_service.py:1338-1344)::

        [{"timestamp": "<now - 30d>", "equity": 100000.0},
         {"timestamp": "<now>",       "equity": 100000.0}]

    Two points that were never recorded, at the starting capital, spanning exactly the window the
    caller asked for. It renders as a chart: a flat line implying thirty days of measured
    break-even performance. `days` is honoured in the fabrication, so asking for 90 days produces
    a longer lie - the synthesis is shaped to be convincing.

    Note the live branch at :1344 already returns `[]` for the same condition. The two branches
    disagree about what "no rows" means, and the paper one is the outlier.
    """
    questdb = _EmptyQuestDB()
    with patch.object(DashboardAggregationService, "_get_telemetry", return_value=questdb):
        curve = await dashboard_service.get_equity_curve(
            mock_user, days=30, environment="paper"
        )

    assert questdb.queries, "the test must have actually reached QuestDB for this to mean anything"
    assert curve == [], (
        f"no equity rows exist, so the series is empty. `F` synthesises {len(curve)} point(s): "
        f"{json.dumps(curve)}"
    )


@pytest.mark.asyncio
async def test_live_equity_curve_with_no_rows_is_already_empty(mock_user, dashboard_service):
    """Preservation, and the proof that `[]` is this codebase's own convention for the condition.

    Passes on `F`. It is here so the paper expectation above is visibly the live behaviour applied
    consistently, rather than a preference introduced by this spec.
    """
    with patch.object(
        DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
    ):
        curve = await dashboard_service.get_equity_curve(mock_user, days=30, environment="live")

    assert curve == []


# ══════════════════════════════════════════════════════════════════════════
# 4. AN ABSENT COLUMN ON THE PAPER ROW  (`get_portfolio_overview` :897-903, Requirement 1.6)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "omitted, derived_fields",
    [
        # :897  total_equity     = float(acct.get("total_equity", 100000.0))
        ("total_equity", ("total_equity", "total_value")),
        # :898  available_balance = float(acct.get("available_balance", 100000.0))
        #       free_balance      = available_balance          (:899, carried)
        ("available_balance", ("available_balance", "free_balance")),
        # :903  initial_capital  = float(acct.get("initial_capital", 100000.0))
        #       -> the DENOMINATOR of today_return_pct, so the fabrication is divided by rather
        #          than published, and never appears as `100000.0` in the body
        ("initial_capital", ("today_return_pct",)),
    ],
)
@pytest.mark.asyncio
async def test_an_absent_paper_account_column_is_not_filled_in_with_the_starting_capital(
    mock_user, dashboard_service, omitted, derived_fields
):
    """The read SUCCEEDED and the column is not there. That is still "not read".

    COUNTEREXAMPLE OBSERVED ON `F` - `float(acct.get(<column>, 100000.0))` wins, per column:

        total_equity absent      -> total_equity: 100000.0, total_value: 100000.0
        available_balance absent -> available_balance: 100000.0, free_balance: 100000.0
        initial_capital absent   -> today_return_pct: 0.5

    The `initial_capital` case is the subtle one and the reason this is parametrised over three
    columns rather than asserting the literal once. The fabricated `100000.0` is a *divisor*
    (`today_pnl / initial_capital * 100`), so it never appears in the response: with the stub's
    `unrealized_pnl` of `500.0`, `F` publishes `today_return_pct: 0.5` - a precise-looking
    percentage of a capital base nobody read. A `100000.0`-hunting assertion alone would pass
    this case and declare the column safe.

    An absent column is a schema or migration fault, which is exactly when a stale-looking
    `100000.0` is most likely to be believed.
    """
    row = {key: value for key, value in COMPLETE_PAPER_ROW.items() if key != omitted}
    assert omitted not in row, "the fixture must actually omit the column"

    with _paper_account_row(row):
        overview = await dashboard_service.get_portfolio_overview(
            mock_user, environment="paper"
        )

    for field in derived_fields:
        assert overview[field] is None, (
            f"the paper account row has no {omitted!r}, so {field} was not read and must be "
            f"None. `F` returns {overview[field]!r} - synthesised by "
            f"float(acct.get({omitted!r}, {FABRICATED_CAPITAL}))."
        )

    if omitted != "initial_capital":
        sites = _fabricated_capital_sites(overview)
        assert sites == [], (
            f"{omitted!r} is absent from the row, yet {FABRICATED_CAPITAL} was published at "
            f"{sites}"
        )


@pytest.mark.asyncio
async def test_a_paper_row_that_is_complete_reports_every_figure_it_read(
    mock_user, dashboard_service
):
    """Preservation. A present column is reported as read, including the `0.0`s.

    Passes on `F` and must keep passing: the fix must distinguish "absent" from "zero", not
    replace one blanket answer with another.
    """
    with _paper_account_row(dict(COMPLETE_PAPER_ROW)):
        overview = await dashboard_service.get_portfolio_overview(
            mock_user, environment="paper"
        )

    assert overview["total_equity"] == 4200.0
    assert overview["total_value"] == 4200.0
    assert overview["available_balance"] == 4200.0
    assert overview["free_balance"] == 4200.0
    assert overview["used_balance"] == 0.0, "a real zero stays zero"
    assert overview["unrealized_pnl"] == 500.0
    # today_pnl 500.0 against initial_capital 4000.0 -> 12.5%, measured, not invented.
    assert overview["today_return_pct"] == 12.5


# ══════════════════════════════════════════════════════════════════════════
# 5. THE REFUTATION PROBE  (`design.md` §Hypothesized Root Cause, wave 1 step 1)
# ══════════════════════════════════════════════════════════════════════════


@requires_routers
@pytest.mark.asyncio
async def test_whether_the_composer_is_the_only_gate_forcing_a_number(
    mock_user, dashboard_service
):
    """Does a `None` survive the trip from `get_portfolio_overview` to the response body?

    `design.md` hypothesises that `float(portfolio.get(key, 0.0))` in the composer's `overview`
    block is the ONLY site that would refuse a `None`, so retyping it is the whole of wave 1
    step 1. This probe hands the composer the fixed-state portfolio - four money figures `None`,
    every other figure a genuine number - and reports what happens.

    HOW TO READ THE RESULT

    * **FAILS with "COMPOSER HYPOTHESIS HELD"** - a `TypeError` was raised, the report names the
      exact line that raised, and wave 1 step 1 is scoped as designed. This is the expected
      outcome on `F`.
    * **FAILS on the `None` assertions** - the `None` reached the response but was coerced
      somewhere en route (a `0.0`, a `100000.0`). Another gate exists. Wave 1 is larger.
    * **PASSES** - the `None`s arrived intact and nothing raised. **The hypothesis is REFUTED**
      and the composer is not a gate at all; re-hypothesise before writing task 6.1.

    The traceback is reduced to the deepest frame inside the service, because *which* line refuses
    the `None` is the finding - one raising site means one thing to retype, several means the wave
    has more than one step.

    RESULT OBSERVED ON `F`: **HELD.** ::

        TypeError: float() argument must be a string or a real number, not 'NoneType'
        refused at dashboard_aggregation_service.py:1802
            "total_value": float(portfolio.get("total_value", portfolio.get("total_equity", 0.0))),

    The composer's `overview` block is a real gate, and `get_dashboard_data`'s outer `except`
    re-raises (:1889-1890), so a `None` money figure 500s the whole dashboard rather than
    surfacing. Two consequences for task 6.1: the coercion cannot be removed from one line at a
    time without the next `float()` in the same dict literal raising in its place, and until every
    one of them is retyped this file's section 2 cannot pass. The gate is at :1798-1815 - the
    fallback at :1692-1695 supplies the fabricated number, this block enforces that it be one.
    """
    fixed_state_portfolio = dict(_genuine_zero_portfolio("live"))
    for key in OVERVIEW_MONEY_KEYS:
        fixed_state_portfolio[key] = None

    try:
        with _portfolio_overview_returning(
            fixed_state_portfolio
        ), _no_cached_balances(), patch.object(
            DashboardAggregationService, "_get_telemetry", return_value=_EmptyQuestDB()
        ):
            data = await dashboard_service.get_dashboard_data(
                mock_user, equity_days=30, environment="live"
            )
    except TypeError as exc:
        pytest.fail(
            "COMPOSER HYPOTHESIS HELD. A None money figure cannot reach the response: "
            f"{type(exc).__name__}: {exc}\n"
            f"    refused at: {_deepest_service_frame(exc)}\n"
            "    => the coercion is a real gate and wave 1 step 1 is scoped as design.md has it."
        )

    overview = data["overview"]
    for key in OVERVIEW_MONEY_KEYS:
        assert overview[key] is None, (
            "HYPOTHESIS PARTIALLY REFUTED: no TypeError was raised, so the coercion is not a "
            f"gate - but {key} arrived as {overview[key]!r} instead of None, so something else "
            "on this path substitutes a number. Wave 1 is larger than designed; find that site "
            "before writing task 6.1."
        )
