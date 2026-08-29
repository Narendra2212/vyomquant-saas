"""
tests/test_task_13_1_signal_trace_list.py

Task 13.1 - ``GET /api/signal-trace/signals``.

Requirements 17.1, 17.2, 17.5, 17.7, 20.1, 20.2, 21.3, and 16.2's reconciliation on the
read path. Five things are under test and nothing else:

  1. ROUTE RESOLUTION, against the REAL ``backend_app.main.app``. The literal
     ``/signals/export`` must win over ``/signals/{signal_id}``. Asserted through
     Starlette's own matcher on the mounted app rather than on a router assembled here,
     because the bug this guards against (``GET /api/signals/export``, shadowed by
     ``GET /api/signals/{signal_id}``; and the three ``/api/library/*`` paths before it)
     is a REGISTRATION-ORDER bug and a locally-built router cannot reproduce it.

  2. THE FILTER SEMANTICS. AND across categories, OR within one (Requirement 17.2),
     asserted on the predicates that actually reach the query builder and on the rows
     that come back.

  3. PAGINATION. Page size capped at 100, ``has_more``/``next_offset`` reachable
     (Requirement 17.5).

  4. OWNERSHIP. A non-owner's request produces the byte-identical response a
     nonexistent identifier produces (Requirements 20.1, 20.2).

  5. THE 005b DEGRADATION. Without ``signals.order_lifecycle_state`` the list still
     answers, the state is reconciled through Requirement 16.2's ``SIGNALS_STATUS_MAP``,
     and the warning names ``005b_signal_lifecycle_and_idempotency.sql``.

What is NOT tested here, because it is not this task's:
  * ``GET /signals/{signal_id}``'s trace detail - task 13.2.
  * ``GET /signals/export``'s CSV/JSON body - task 13.3. Only its REACHABILITY is
    asserted here, because that is task 13.1's structural obligation to it.
  * The universal-quantifier versions of the filter and pagination claims - tasks 13.4
    and 13.5 (Properties 15 and 5). The cases here are the concrete examples.
"""

import json

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Match

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    ORDER_LIFECYCLE_STATE_VALUES,
    SIGNALS_STATUS_MAP,
    OrderLifecycleRejected,
    OrderLifecycleState,
)
from backend_app.backend.signal_service import (
    LEGACY_STATUS_SPELLINGS,
    LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING,
    SIGNAL_LIFECYCLE_MIGRATION,
    SIGNAL_TRACE_PAGE_SIZE,
    SignalService,
    mint_signal,
    signal_trace_item,
)

OWNER = {"id": "user-aaaa", "access_token": "token-aaaa"}
INTRUDER = {"id": "user-zzzz", "access_token": "token-zzzz"}


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES - a fake PostgREST client that actually applies the predicates
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class FakeQuery:
    """Records every predicate and then APPLIES it, so a filter claim is a real claim.

    A recording-only double would let a wrong predicate pass as long as it was recorded,
    which is exactly the class of bug Requirement 17.2 is about.
    """

    def __init__(self, client, columns):
        self.client = client
        self.columns = columns
        self.predicates = []
        self._negate = False
        self._range = None
        self._limit = None
        self._desc = False

    # ── predicate builders ──────────────────────────────────────────────
    def eq(self, column, value):
        self.predicates.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.predicates.append(("in", column, list(values)))
        return self

    def gte(self, column, value):
        self.predicates.append(("gte", column, value))
        return self

    def lte(self, column, value):
        self.predicates.append(("lte", column, value))
        return self

    def is_(self, column, value):
        self.predicates.append(("is_not" if self._negate else "is", column, value))
        self._negate = False
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def order(self, column, desc=False):
        self._order_column = column
        self._desc = desc
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # ── execution ───────────────────────────────────────────────────────
    def execute(self):
        self.client.queries.append(self)
        if not self.client.lifecycle_columns and "order_lifecycle_state" in self.columns:
            raise Exception(
                'ERROR: 42703: column signals.order_lifecycle_state does not exist'
            )
        if self._limit is not None and not self.predicates:
            # the 005b probe
            return _Result(data=[])

        rows = [row for row in self.client.rows if self._matches(row)]
        rows.sort(key=lambda r: r.get("generated_at") or "", reverse=self._desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        return _Result(data=[self._project(row) for row in rows])

    def _project(self, row):
        if self.client.lifecycle_columns:
            return dict(row)
        return {k: v for k, v in row.items() if k != "order_lifecycle_state"}

    def _matches(self, row):
        for kind, column, value in self.predicates:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
            if kind == "gte" and (actual or "") < value:
                return False
            if kind == "lte" and (actual or "") > value:
                return False
            if kind == "is" and actual is not None:
                return False
            if kind == "is_not" and actual is None:
                return False
        return True


class FakeSupabase:
    def __init__(self, rows, *, lifecycle_columns=True):
        self.rows = rows
        self.lifecycle_columns = lifecycle_columns
        self.queries = []

    def table(self, name):
        assert name == "signals"
        return self

    def select(self, columns):
        return FakeQuery(self, columns)


def service_for(rows, *, lifecycle_columns=True):
    """A ``SignalService`` whose client is the fake. Fresh per test - no singleton."""
    service = SignalService()
    client = FakeSupabase(rows, lifecycle_columns=lifecycle_columns)

    async def _client(_user):
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


def signal_row(
    signal_id,
    *,
    user_id=OWNER["id"],
    strategy_id="strat-1",
    strategy_version="v1",
    deployment_id="dep-1",
    symbol="BTC/USDT",
    decision="BUY",
    state="GENERATED",
    status="pending",
    generated_at="2024-05-01T12:00:00+00:00",
):
    """One ``public.signals`` row, in the shape ``Signal.to_row`` writes."""
    return {
        "id": signal_id,
        "user_id": user_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "deployment_id": deployment_id,
        "exchange_id": "kraken",
        "symbol": symbol,
        "timeframe": "1h",
        "worker_id": "worker-7",
        "decision": decision,
        "status": status,
        "order_lifecycle_state": state,
        "quantity": 0.25,
        "generated_at": generated_at,
        "indicators": {"rsi-1": 28.4},
        "market_info": {
            "signal_type": "ENTRY" if decision in ("BUY", "SELL") else "EXIT",
            "side": decision if decision in ("BUY", "SELL") else None,
            "mode": "paper",
            "strategy_version_id": "ver-1",
            "exchange_account_id": "acct-1",
            "source_node_ids": ["action-1"],
            "closure_ready": True,
            "sizing_intention": None,
            "price": 61234.5,
        },
        "ml_info": None,
        "risk_passed": True,
        "risk_reason": "within limits",
        "position_size": 0.25,
        "risk_evaluated_at": "2024-05-01T12:00:01+00:00",
        "order_id": None,
        "order_status": None,
        "filled": None,
        "remaining": None,
        "average_price": None,
        "fees": None,
        "slippage": None,
        "latency_ms": None,
        "trade_id": None,
        "pnl": None,
        "realized_pnl": None,
        "order_updated_at": None,
        "executed_at": None,
    }


@pytest.fixture(autouse=True)
def _forget_migration_verdict():
    """Each test probes 005b for itself: the verdict is cached per process."""
    svc.reset_signal_lifecycle_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()


def predicates_of(client):
    """Every predicate from the LIST query (the last one, after the 005b probe)."""
    listing = [q for q in client.queries if q.predicates]
    assert listing, "no filtered query reached the client"
    return listing[-1].predicates


# ══════════════════════════════════════════════════════════════════════════
# 1. ROUTE RESOLUTION AGAINST THE REAL APP
# ══════════════════════════════════════════════════════════════════════════


def _resolve(path, method="GET"):
    """The route the MOUNTED app would actually dispatch ``path`` + ``method`` to.

    ``Match.FULL`` only: a path-match with the wrong method is ``Match.PARTIAL``, and
    Starlette keeps looking, so accepting PARTIAL here would report a different route than
    the one that actually handles the request.
    """
    from backend_app.main import app

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in app.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route
    return None


def test_export_path_is_not_shadowed_by_the_signal_id_path():
    """The bug that killed GET /api/signals/export must not repeat under /signal-trace.

    ``/{signal_id}`` matches ANY single segment, "export" included, so registration order
    decides whether the export endpoint exists at all.
    """
    from backend_app.routers.signal_trace import SIGNAL_TRACE_LITERAL_PATHS

    for path in SIGNAL_TRACE_LITERAL_PATHS:
        route = _resolve(path)
        assert route is not None, f"{path} resolves to nothing"
        assert route.path == path, (
            f"{path} is shadowed by {route.path!r} ({route.name}). A literal sub-path of "
            "/signals must be registered BEFORE /signals/{signal_id} - see the route-order "
            "banner in backend_app/routers/signal_trace.py."
        )


def test_every_literal_signal_trace_route_resolves_to_itself():
    """The structural guard tasks 13.2 and 13.3 inherit automatically.

    Derived from the app's own registrations rather than from a hand-kept list, so a
    literal route added later — by any task — is checked without this test being edited.
    """
    from backend_app.main import app

    literal_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/signal-trace/signals")
        and "{" not in route.path
    ]
    assert literal_routes, "no literal signal-trace routes found; the prefix moved"

    shadowed = []
    for route in literal_routes:
        for method in sorted(route.methods):
            resolved = _resolve(route.path, method)
            if resolved is None or resolved.path != route.path:
                shadowed.append(
                    f"{method} {route.path} -> "
                    f"{resolved.path if resolved else None}"
                )
    assert not shadowed, (
        "these literal routes are shadowed by an earlier parameterised route and are "
        f"unreachable: {shadowed}. Move them above the PARAMETERISED PATHS banner in "
        "backend_app/routers/signal_trace.py."
    )


def test_signal_trace_list_route_exists_on_the_mounted_app():
    route = _resolve("/api/signal-trace/signals")
    assert route is not None and route.name == "list_signals"


# ══════════════════════════════════════════════════════════════════════════
# 2. THE FILTER SEMANTICS (Requirement 17.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_single_value_filter_is_an_equality_predicate():
    service, client = service_for([signal_row("s1")])
    await service.list_signal_trace(OWNER, symbol="BTC/USDT")
    assert ("eq", "symbol", "BTC/USDT") in predicates_of(client)


@pytest.mark.asyncio
async def test_multiple_values_in_one_category_are_ORed():
    rows = [
        signal_row("s1", symbol="BTC/USDT"),
        signal_row("s2", symbol="ETH/USDT"),
        signal_row("s3", symbol="SOL/USDT"),
    ]
    service, client = service_for(rows)

    page = await service.list_signal_trace(OWNER, symbol=["BTC/USDT", "ETH/USDT"])

    assert ("in", "symbol", ["BTC/USDT", "ETH/USDT"]) in predicates_of(client)
    assert {item["symbol"] for item in page["signals"]} == {"BTC/USDT", "ETH/USDT"}


@pytest.mark.asyncio
async def test_separate_categories_are_ANDed():
    """A signal is included only if it matches at least one value in EVERY category."""
    rows = [
        signal_row("btc-buy", symbol="BTC/USDT", decision="BUY"),
        signal_row("btc-sell", symbol="BTC/USDT", decision="SELL"),
        signal_row("eth-buy", symbol="ETH/USDT", decision="BUY"),
        signal_row("sol-buy", symbol="SOL/USDT", decision="BUY"),
    ]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(
        OWNER, symbol=["BTC/USDT", "ETH/USDT"], side=["BUY"]
    )

    assert {item["id"] for item in page["signals"]} == {"btc-buy", "eth-buy"}


@pytest.mark.asyncio
async def test_side_filters_the_decision_column():
    """public.signals has no `side` column; 005b's own header calls `decision` the side."""
    service, client = service_for([signal_row("s1")])
    await service.list_signal_trace(OWNER, side=["BUY", "SELL"])
    assert ("in", "decision", ["BUY", "SELL"]) in predicates_of(client)


@pytest.mark.asyncio
async def test_inactive_category_adds_no_predicate_and_constrains_nothing():
    rows = [signal_row("s1", symbol="BTC/USDT"), signal_row("s2", symbol="ETH/USDT")]
    service, client = service_for(rows)

    page = await service.list_signal_trace(OWNER)

    assert not any(column == "symbol" for _, column, _ in predicates_of(client))
    assert page["filters_active"] is False
    assert len(page["signals"]) == 2


@pytest.mark.asyncio
async def test_empty_string_filter_is_not_an_active_category():
    """?symbol= is how a cleared form field arrives; it must not filter everything away."""
    service, _ = service_for([signal_row("s1")])
    page = await service.list_signal_trace(OWNER, symbol=[""], strategy_id="")
    assert page["filters_active"] is False
    assert len(page["signals"]) == 1


@pytest.mark.asyncio
async def test_every_requirement_17_2_category_is_filterable():
    rows = [
        signal_row(
            "wanted",
            strategy_id="strat-1",
            strategy_version="v2",
            deployment_id="dep-9",
            symbol="BTC/USDT",
            decision="SELL",
            state="EXECUTED",
        ),
        signal_row("other", strategy_id="strat-2", strategy_version="v1"),
    ]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(
        OWNER,
        strategy_id=["strat-1"],
        strategy_version=["v2"],
        deployment_id=["dep-9"],
        symbol=["BTC/USDT"],
        side=["SELL"],
        order_lifecycle_state=["EXECUTED"],
        date_from="2024-05-01T00:00:00+00:00",
        date_to="2024-05-02T00:00:00+00:00",
    )

    assert [item["id"] for item in page["signals"]] == ["wanted"]
    assert set(page["active_filters"]) == {
        "strategy_id",
        "strategy_version",
        "deployment_id",
        "symbol",
        "side",
        "order_lifecycle_state",
        "date_from",
        "date_to",
    }


@pytest.mark.asyncio
async def test_lifecycle_state_filter_accepts_any_case_and_either_separator():
    service, client = service_for([signal_row("s1", state="PARTIALLY_EXECUTED")])
    await service.list_signal_trace(
        OWNER, order_lifecycle_state=["partially-executed", "executed"]
    )
    assert (
        "in",
        "order_lifecycle_state",
        ["PARTIALLY_EXECUTED", "EXECUTED"],
    ) in predicates_of(client)


@pytest.mark.asyncio
async def test_unrecognised_lifecycle_state_is_refused_not_ignored():
    """Ignoring it would return MORE signals than the caller asked for."""
    service, _ = service_for([signal_row("s1")])

    with pytest.raises(OrderLifecycleRejected) as excinfo:
        await service.list_signal_trace(OWNER, order_lifecycle_state=["ALMOST_FILLED"])

    assert excinfo.value.http_status == 400
    assert excinfo.value.code == "ORDER_LIFECYCLE_STATE_UNRECOGNISED"
    assert excinfo.value.details["unrecognised"] == ["ALMOST_FILLED"]
    assert excinfo.value.details["recognised_states"] == list(ORDER_LIFECYCLE_STATE_VALUES)


# ══════════════════════════════════════════════════════════════════════════
# 3. PAGINATION (Requirement 17.5)
# ══════════════════════════════════════════════════════════════════════════


def test_the_page_size_ceiling_is_one_hundred():
    assert SIGNAL_TRACE_PAGE_SIZE == 100


def test_the_route_bounds_limit_at_the_services_own_constant():
    """One number, not two: the route's `le=` reads the service constant."""
    route = _resolve("/api/signal-trace/signals")
    limit = next(p for p in route.dependant.query_params if p.name == "limit")
    bounds = {
        type(constraint).__name__.lower(): getattr(
            constraint, type(constraint).__name__.lower(), None
        )
        for constraint in getattr(limit.field_info, "metadata", [])
    }
    assert bounds.get("le") == SIGNAL_TRACE_PAGE_SIZE, bounds
    assert bounds.get("ge") == 1, bounds
    assert limit.field_info.default == SIGNAL_TRACE_PAGE_SIZE


@pytest.mark.asyncio
async def test_limit_above_the_ceiling_is_clamped_by_the_service_too():
    """Defence in depth: an internal caller bypassing the route cannot ask for 5000."""
    rows = [signal_row(f"s{i}", generated_at=f"2024-05-01T12:{i:02d}:00+00:00") for i in range(120)]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(OWNER, limit=5000)

    assert page["limit"] == SIGNAL_TRACE_PAGE_SIZE
    assert page["count"] == SIGNAL_TRACE_PAGE_SIZE


@pytest.mark.asyncio
async def test_has_more_and_next_offset_reach_every_signal_beyond_the_first_page():
    rows = [
        signal_row(f"s{i:03d}", generated_at=f"2024-05-01T12:{i:02d}:00+00:00")
        for i in range(25)
    ]
    service, _ = service_for(rows)

    seen = []
    offset = 0
    while True:
        page = await service.list_signal_trace(OWNER, limit=10, offset=offset)
        assert page["count"] <= 10
        seen.extend(item["id"] for item in page["signals"])
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + 10
        offset = page["next_offset"]

    assert len(seen) == 25
    assert len(set(seen)) == 25, "a signal appeared on two pages"


@pytest.mark.asyncio
async def test_the_extra_probe_row_is_never_returned():
    rows = [
        signal_row(f"s{i}", generated_at=f"2024-05-01T12:{i:02d}:00+00:00")
        for i in range(5)
    ]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(OWNER, limit=2)

    assert page["count"] == 2
    assert page["has_more"] is True
    assert page["total_is_exact"] is False, "no COUNT was issued; total is a lower bound"


@pytest.mark.asyncio
async def test_signals_are_sorted_newest_first():
    rows = [
        signal_row("old", generated_at="2024-05-01T10:00:00+00:00"),
        signal_row("new", generated_at="2024-05-01T14:00:00+00:00"),
        signal_row("middle", generated_at="2024-05-01T12:00:00+00:00"),
    ]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(OWNER)

    assert [item["id"] for item in page["signals"]] == ["new", "middle", "old"]


# ══════════════════════════════════════════════════════════════════════════
# 4. OWNERSHIP (Requirements 20.1, 20.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_owner_predicate_is_always_present():
    service, client = service_for([signal_row("s1")])
    await service.list_signal_trace(OWNER)
    assert ("eq", "user_id", OWNER["id"]) in predicates_of(client)


@pytest.mark.asyncio
async def test_a_non_owner_sees_none_of_the_owners_signals():
    rows = [signal_row("s1"), signal_row("s2")]
    service, _ = service_for(rows)

    page = await service.list_signal_trace(INTRUDER)

    assert page["signals"] == []


@pytest.mark.asyncio
async def test_a_non_owner_filtering_by_a_real_strategy_gets_the_missing_resource_response():
    """Requirement 20.2: no aspect of the response may reveal that the strategy exists."""
    rows = [signal_row("s1", strategy_id="strat-1")]

    service, _ = service_for(rows)
    foreign = await service.list_signal_trace(INTRUDER, strategy_id="strat-1")

    service, _ = service_for(rows)
    nonexistent = await service.list_signal_trace(
        INTRUDER, strategy_id="strategy-that-does-not-exist"
    )

    assert foreign["signals"] == []
    # Byte-identical apart from the identifier the caller itself supplied.
    assert json.dumps(foreign, sort_keys=True) == json.dumps(nonexistent, sort_keys=True)


@pytest.mark.asyncio
async def test_the_owner_still_sees_their_own_signals():
    """The ownership assertions above must not be passing because everything is empty."""
    service, _ = service_for([signal_row("s1", strategy_id="strat-1")])
    page = await service.list_signal_trace(OWNER, strategy_id="strat-1")
    assert [item["id"] for item in page["signals"]] == ["s1"]


# ══════════════════════════════════════════════════════════════════════════
# 5. THE 005b DEGRADATION AND REQUIREMENT 16.2's RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════


def test_the_legacy_spelling_table_is_derived_from_signals_status_map():
    """Not transcribed - so it cannot drift from Requirement 16.2's own table."""
    for legacy_word, state in SIGNALS_STATUS_MAP.items():
        assert legacy_word in LEGACY_STATUS_SPELLINGS[state]
    assert set(LEGACY_STATUS_SPELLINGS[OrderLifecycleState.CANCELLED]) == {
        "cancelled",
        "expired",
    }


def test_three_canonical_states_have_no_legacy_spelling():
    assert set(LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING) == {
        OrderLifecycleState.GENERATED,
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.CLOSED,
    }


@pytest.mark.asyncio
async def test_without_005b_the_list_still_answers_and_names_the_migration(caplog):
    rows = [signal_row("s1", state=None, status="executed")]
    service, _ = service_for(rows, lifecycle_columns=False)

    with caplog.at_level("WARNING"):
        page = await service.list_signal_trace(OWNER, order_lifecycle_state=["EXECUTED"])

    assert page["lifecycle_state_source"] == "legacy_status_map"
    assert page["degraded"]["migration"] == SIGNAL_LIFECYCLE_MIGRATION
    assert "005b_signal_lifecycle_and_idempotency.sql" in caplog.text
    assert [item["id"] for item in page["signals"]] == ["s1"]


@pytest.mark.asyncio
async def test_without_005b_the_lifecycle_filter_runs_on_the_legacy_status_column():
    """Requirement 16.2's map, run backwards, so pagination stays a database fact."""
    rows = [
        signal_row("executed", state=None, status="executed"),
        signal_row("pending", state=None, status="pending"),
        signal_row("expired", state=None, status="expired"),
        signal_row("cancelled", state=None, status="cancelled"),
    ]
    service, client = service_for(rows, lifecycle_columns=False)

    page = await service.list_signal_trace(OWNER, order_lifecycle_state=["CANCELLED"])

    predicates = predicates_of(client)
    assert not any(column == "order_lifecycle_state" for _, column, _ in predicates)
    assert ("in", "status", ["cancelled", "expired"]) in predicates
    assert {item["id"] for item in page["signals"]} == {"expired", "cancelled"}
    # And every returned row reports the canonical value, not the legacy word.
    assert {item["order_lifecycle_state"] for item in page["signals"]} == {"CANCELLED"}


@pytest.mark.asyncio
async def test_without_005b_a_state_with_no_legacy_spelling_returns_an_honest_empty_page():
    rows = [signal_row("s1", state=None, status="pending")]
    service, _ = service_for(rows, lifecycle_columns=False)

    page = await service.list_signal_trace(OWNER, order_lifecycle_state=["CLOSED"])

    assert page["signals"] == []
    assert page["filters_active"] is True
    assert page["degraded"]["unsupported_lifecycle_states"] == ["CLOSED"]


@pytest.mark.asyncio
async def test_with_005b_applied_nothing_is_reported_as_degraded():
    service, _ = service_for([signal_row("s1", state="EXECUTED")])
    page = await service.list_signal_trace(OWNER, order_lifecycle_state=["EXECUTED"])
    assert page["lifecycle_state_source"] == "canonical"
    assert page["degraded"] is None


# ══════════════════════════════════════════════════════════════════════════
# 6. THE SHAPE - one projection, rendered through Signal.to_public_dict
# ══════════════════════════════════════════════════════════════════════════


def minted_signal():
    deployment = {
        "id": "dep-1111",
        "user_id": OWNER["id"],
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "paper",
        "worker_id": "worker-7",
        "api_secret": "s3cr3t-shhh",
    }
    node_output = {
        "decision": "BUY",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "quantity": 0.25,
        "price": 61234.5,
        "bar_time": "2024-05-01T12:00:00+00:00",
        "source_node_ids": ["action-1"],
        "closure_ready": True,
        "node_closure": {"rsi-1": 28.4},
        "risk_validation": {
            "passed": True,
            "reason": "within limits",
            "position_size": 0.25,
            "capital": 10000.0,
            "evaluated_at": "2024-05-01T12:00:01+00:00",
        },
    }
    return mint_signal(deployment, node_output)


def test_a_persisted_row_renders_back_into_the_public_shape():
    """The row is the inverse of to_row(), so one shape reaches the frontend."""
    signal = minted_signal()
    item = signal_trace_item(signal.to_row())

    expected = signal.to_public_dict()
    for key, value in expected.items():
        assert item[key] == value, f"{key} did not survive the round trip"


def test_the_list_item_carries_the_execution_outcome_columns():
    row = signal_row("s1", state="EXECUTED")
    row.update(
        {
            "order_id": "ord-1",
            "trade_id": "trd-1",
            "average_price": 61000.0,
            "filled": 0.25,
            "remaining": 0.0,
            "fees": 1.23,
            "executed_at": "2024-05-01T12:05:00+00:00",
        }
    )

    execution = signal_trace_item(row)["execution"]

    assert execution["order_id"] == "ord-1"
    assert execution["execution_id"] == "trd-1"
    assert execution["execution_price"] == 61000.0
    assert execution["filled_quantity"] == 0.25
    assert execution["remaining_quantity"] == 0.0
    assert execution["fees"] == 1.23
    assert execution["failure_reason"] is None, "an EXECUTED signal has no failure reason"


def test_a_failed_signal_reports_a_failure_reason():
    row = signal_row("s1", state="REJECTED", status="rejected")
    row["risk_reason"] = "max drawdown exceeded"
    assert signal_trace_item(row)["execution"]["failure_reason"] == "max drawdown exceeded"


def test_no_credential_reaches_the_rendered_item():
    """Requirement 20.3 holds on the wire because the record has no field for one."""
    item = signal_trace_item(minted_signal().to_row())
    assert "s3cr3t-shhh" not in json.dumps(item, default=str)
    assert item["exchange_account_id"] == "acct-dddd"  # the exempt internal reference
