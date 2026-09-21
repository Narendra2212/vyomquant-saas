"""
tests/test_task_29_3_signal_environment_filter.py - task 29.3.

Spec: marketplace-subscriptions-paper-trading. Requirements 23.2, 23.4, 23.6.

WHAT TASK 29.3 IS
    ``GET /api/signal-trace/signals``, ``/signals/export`` and ``/signals/{signal_id}`` gain
    an ``environment`` filter. It is "one more of the same": declared as a LIST exactly like
    the Requirement 17.2 filter categories ``routers/signal_trace.py`` already declares, so
    ``?environment=PAPER&environment=LIVE`` collects into BOTH values instead of keeping only
    the last, OR within the category and AND against every other category. The default is
    untouched - Requirement 23.6's caller's own signals across all environments - and no
    total or aggregate mixes ``PAPER`` with ``LIVE`` without an explicit label
    (Requirement 23.4).

A SIBLING OF ``tests/test_task_29_signal_environments.py``, NOT A SIXTH SECTION OF IT
    That module is the WRITE path: its ``FakeSignalsClient`` records a migration probe and an
    INSERT, and asserts that nothing touches a table other than ``signals``. This task is the
    READ path, and needs a double that APPLIES predicates and pages - the shape
    ``tests/test_task_13_1_signal_trace_list.py`` established for exactly this reason, since a
    recording-only double lets a wrong predicate pass as long as it was recorded. Keeping the
    two apart also leaves 29.1/29.2's 47 tests as an untouched control.

WHAT IS ASSERTED HERE, SECTION BY SECTION
    1. THE DECLARATION. The parameter exists on all three routes, is a LIST on each, and the
       three route declarations agree - asserted against the MOUNTED app, so a filter added to
       the service but not to a route (or to two routes and not the third) fails here.
    2. THE PREDICATE. One value is an ``eq``, two are one ``in`` - IN THE STATEMENT, next to
       the ownership predicate, and never a post-filter over rows already fetched. The
       distinction is not cosmetic: ``limit``/``offset`` are applied by the database, so a
       filter applied after retrieval pages the wrong rows.
    3. THE REFUSAL. A value outside ``BACKTEST``/``PAPER``/``LIVE`` is refused with 400,
       never ignored - ignoring it would answer with every environment's signals.
    4. THE LABEL (Requirement 23.4). Every item carries its own ``environment``, and the
       envelope carries ``count_by_environment``, so no figure on the page is an unlabelled
       ``PAPER``+``LIVE`` mixture.
    5. THE PRE-010 DEGRADATION. The filter cannot be applied, so it is not applied and the
       response SAYS SO: an empty page naming ``010_signal_environment.sql``, and the same
       statement in the body of the detail view's 404. Never an unfiltered list passed off as
       a filtered one, and never a silent empty one.
    6. THE DEFAULT, UNCHANGED (Requirement 23.6).
    7. THE EXPORT AND THE DETAIL, through the mounted app.

``_run_coroutine`` is imported from ``tests/test_paper_order_lifecycle_writes.py`` rather than
re-derived, and no test here calls ``asyncio.run``: see that module's ``_HARNESS_LOOP`` note -
a fresh loop per call exhausts the Windows loopback port range.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Match

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import signal_service as svc
from backend_app.backend.execution_environment import EXECUTION_ENVIRONMENTS
from backend_app.backend.signal_service import (
    ENVIRONMENT_SOURCE_COLUMN,
    ENVIRONMENT_SOURCE_UNAVAILABLE,
    SIGNAL_ENVIRONMENT_COLUMNS,
    SIGNAL_ENVIRONMENT_MIGRATION,
    SIGNAL_TRACE_EXPORT_COLUMNS,
    UNLABELLED_ENVIRONMENT,
    SignalEnvironmentFilterUnanswerable,
    SignalEnvironmentRefused,
    SignalService,
    environment_counts,
    resolve_environment_filter,
    signal_trace_item,
)
from tests.test_paper_order_lifecycle_writes import _run_coroutine

OWNER = {"id": "user-aaaa", "access_token": "token-aaaa"}

#: A ``42703`` as PostgREST reports it - one column named, which is why the pair is probed as
#: a set and why a filter on the missing column cannot simply be sent and hoped for.
MISSING_ENVIRONMENT = (
    "{'code': '42703', 'message': 'column signals.environment does not exist'}"
)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES - a fake PostgREST client that APPLIES the predicates it is given
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data: Any = None, error: Any = None) -> None:
        self.data = data
        self.error = error


class FakeQuery:
    """Records every predicate and then applies it. A recorded-but-wrong predicate must fail."""

    def __init__(self, client: "FakeSupabase", table: str, columns: str) -> None:
        self.client = client
        self.table_name = table
        self.columns = columns
        self.predicates: List[Any] = []
        self._negate = False
        self._range: Optional[Any] = None
        self._limit: Optional[int] = None
        self._desc = False

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.predicates.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "FakeQuery":
        self.predicates.append(("in", column, list(values)))
        return self

    def gte(self, column: str, value: Any) -> "FakeQuery":
        self.predicates.append(("gte", column, value))
        return self

    def lte(self, column: str, value: Any) -> "FakeQuery":
        self.predicates.append(("lte", column, value))
        return self

    def is_(self, column: str, value: Any) -> "FakeQuery":
        self.predicates.append(("is_not" if self._negate else "is", column, value))
        self._negate = False
        return self

    @property
    def not_(self) -> "FakeQuery":
        self._negate = True
        return self

    def order(self, column: str, desc: bool = False) -> "FakeQuery":
        self._desc = desc
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        self._range = (start, end)
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._limit = n
        return self

    def execute(self) -> Any:
        self.client.queries.append(self)

        named = {name.strip() for name in str(self.columns).split(",")}
        if self.table_name == svc.STRATEGY_OWNER_TABLE:
            rows = [row for row in self.client.strategies if self._matches(row)]
            return _Result(data=[dict(row) for row in rows])

        assert self.table_name == "signals", f"nothing here may touch {self.table_name}"

        if self.client.missing_environment and named & set(SIGNAL_ENVIRONMENT_COLUMNS):
            raise Exception(MISSING_ENVIRONMENT)
        if self._limit is not None and not self.predicates:
            return _Result(data=[])  # a migration probe

        rows = [row for row in self.client.rows if self._matches(row)]
        rows.sort(key=lambda r: r.get("generated_at") or "", reverse=self._desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        return _Result(data=[self._project(row) for row in rows])

    def _project(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if self.client.missing_environment:
            return {
                key: value
                for key, value in row.items()
                if key not in SIGNAL_ENVIRONMENT_COLUMNS
            }
        return dict(row)

    def _matches(self, row: Dict[str, Any]) -> bool:
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
    def __init__(self, rows: Any, *, missing_environment: bool = False) -> None:
        self.rows = list(rows)
        self.missing_environment = missing_environment
        self.queries: List[FakeQuery] = []
        #: The caller wrote the strategy these signals belong to, so task 29.4's viewer-role
        #: resolution answers "owner" and the detail view is the full one. The subscriber side
        #: of that decision is ``tests/test_signal_trace_subscriber_projection.py``.
        self.strategies = [{"id": "strat-1", "user_id": OWNER["id"]}]
        self._table: Optional[str] = None

    def table(self, name: str) -> "FakeSupabase":
        self._table = name
        return self

    def select(self, columns: str) -> FakeQuery:
        return FakeQuery(self, self._table or "", columns)


def service_for(rows: Any, *, missing_environment: bool = False):
    service = SignalService()
    client = FakeSupabase(rows, missing_environment=missing_environment)

    async def _client(_user: Any) -> Any:
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


def signal_row(
    signal_id: str,
    *,
    environment: Optional[str] = "LIVE",
    paper_session_id: Optional[str] = None,
    user_id: str = OWNER["id"],
    symbol: str = "BTC/USDT",
    decision: str = "BUY",
    state: str = "GENERATED",
    generated_at: str = "2024-05-01T12:00:00+00:00",
) -> Dict[str, Any]:
    """One ``public.signals`` row in the shape ``Signal.to_row`` writes, 010 included."""
    return {
        "id": signal_id,
        "user_id": user_id,
        "strategy_id": "strat-1",
        "strategy_version": "v3",
        "deployment_id": None if environment == "PAPER" else "dep-1",
        "exchange_id": "kraken",
        "symbol": symbol,
        "timeframe": "1h",
        "worker_id": "worker-7",
        "decision": decision,
        "status": "pending",
        "order_lifecycle_state": state,
        "quantity": 0.25,
        "generated_at": generated_at,
        "environment": environment,
        "paper_session_id": paper_session_id,
        "indicators": {"rsi-1": 28.4},
        "market_info": {
            "signal_type": "ENTRY",
            "side": decision,
            "mode": "paper" if environment == "PAPER" else "live",
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


def three_environments() -> List[Dict[str, Any]]:
    """One signal in each environment, newest first when sorted descending."""
    return [
        signal_row("live-1", environment="LIVE", generated_at="2024-05-01T12:00:00+00:00"),
        signal_row(
            "paper-1",
            environment="PAPER",
            paper_session_id="sess-1",
            generated_at="2024-05-01T11:00:00+00:00",
        ),
        signal_row(
            "backtest-1", environment="BACKTEST", generated_at="2024-05-01T10:00:00+00:00"
        ),
    ]


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Both verdicts are cached per process; each test probes for itself."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()


def predicates_of(client: FakeSupabase) -> List[Any]:
    """The predicates of the LIST query - the last query that carried any."""
    listing = [q for q in client.queries if q.predicates and q.table_name == "signals"]
    assert listing, "no filtered query reached the client"
    return listing[-1].predicates


def _resolve(path: str, method: str = "GET") -> Any:
    """The route the MOUNTED app would dispatch ``path`` + ``method`` to."""
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


THE_THREE_READ_PATHS = (
    "/api/signal-trace/signals",
    "/api/signal-trace/signals/export",
    "/api/signal-trace/signals/{signal_id}",
)


def _route_for(path: str) -> APIRoute:
    from backend_app.main import app

    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and "GET" in route.methods
    ]
    assert len(routes) == 1, f"{path} is registered {len(routes)} times"
    return routes[0]


# ══════════════════════════════════════════════════════════════════════════
# 1. THE DECLARATION - a list, on all three routes (Requirement 23.4)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", THE_THREE_READ_PATHS)
def test_all_three_read_paths_declare_the_environment_filter(path):
    names = {p.name for p in _route_for(path).dependant.query_params}
    assert "environment" in names, (
        f"{path} declares no environment filter, so Requirement 23.4's filtering is not "
        f"available there. Declared: {sorted(names)}"
    )


@pytest.mark.parametrize("path", THE_THREE_READ_PATHS)
def test_the_environment_filter_is_declared_as_a_list(path):
    """A scalar keeps only the LAST value, which makes ?environment=PAPER&environment=LIVE a lie."""
    parameter = next(
        p for p in _route_for(path).dependant.query_params if p.name == "environment"
    )
    annotation = str(parameter.field_info.annotation)
    assert "List[str]" in annotation or "list[str]" in annotation, (
        f"{path}'s environment filter is annotated {annotation}; it must be a list, as every "
        "other Requirement 17.2 filter category on this router is"
    )


@pytest.mark.parametrize("path", THE_THREE_READ_PATHS)
def test_the_environment_filter_is_optional_so_the_default_is_unchanged(path):
    """Requirement 23.6: the default is the caller's own signals across ALL environments."""
    parameter = next(
        p for p in _route_for(path).dependant.query_params if p.name == "environment"
    )
    assert not parameter.required
    assert parameter.default is None


def test_the_export_still_accepts_exactly_the_filters_the_list_does():
    """A category on one and not the other would make "export what I see" false."""
    list_params = {p.name for p in _route_for(THE_THREE_READ_PATHS[0]).dependant.query_params}
    export_params = {
        p.name for p in _route_for(THE_THREE_READ_PATHS[1]).dependant.query_params
    }
    assert list_params - export_params == {"limit", "offset"}
    assert export_params - list_params == {"format"}


def test_the_three_accepted_values_are_the_platforms_own_vocabulary():
    """Not a fourth transcription of BACKTEST/PAPER/LIVE."""
    from backend_app.routers.signal_trace import ENVIRONMENT_FILTER_VALUES

    assert ENVIRONMENT_FILTER_VALUES == [member.value for member in EXECUTION_ENVIRONMENTS]
    assert ENVIRONMENT_FILTER_VALUES == ["BACKTEST", "PAPER", "LIVE"]


def test_no_route_takes_a_viewer_role_from_the_request():
    """Requirement 21.1, asserted where a defect would live: the request surface itself.

    Task 29.4's ownership decision is server-side. A ``?viewer_role=owner`` parameter would be
    the whole defect, so no read path may declare one under any of its plausible names.
    """
    forbidden = {"viewer_role", "viewer", "is_owner", "owner_view", "role", "as_owner"}
    for path in THE_THREE_READ_PATHS:
        declared = {p.name for p in _route_for(path).dependant.query_params}
        assert not declared & forbidden, (
            f"{path} accepts {sorted(declared & forbidden)} from the query string; the viewer's "
            "role must come from the authenticated session and the resolved ownership only"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE RESOLVER, AND THE PREDICATE IN THE STATEMENT
# ══════════════════════════════════════════════════════════════════════════


def test_one_value_resolves_to_that_environment():
    assert resolve_environment_filter(["PAPER"]) == (("PAPER",), ())


def test_both_values_resolve_together_and_keep_their_order():
    assert resolve_environment_filter(["PAPER", "LIVE"]) == (("PAPER", "LIVE"), ())


def test_a_repeated_value_is_de_duplicated():
    assert resolve_environment_filter(["LIVE", "LIVE"]) == (("LIVE",), ())


def test_an_empty_string_is_not_an_active_category():
    """``?environment=`` is how a form spells "not filtering", not ``environment = ''``."""
    assert resolve_environment_filter([""]) == ((), ())
    assert resolve_environment_filter(None) == ((), ())


def test_case_and_surrounding_space_resolve():
    """A bookmarked or hand-typed URL legitimately sends ``paper``."""
    assert resolve_environment_filter([" paper "]) == (("PAPER",), ())


def test_an_unknown_value_is_returned_as_unrecognised_and_not_dropped():
    assert resolve_environment_filter(["PAPR"]) == ((), ("PAPR",))


def test_a_single_value_is_an_equality_predicate_in_the_statement():
    service, client = service_for(three_environments())
    page = _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))

    assert ("eq", "environment", "PAPER") in predicates_of(client)
    assert [item["id"] for item in page["signals"]] == ["paper-1"]


def test_both_values_are_one_in_predicate_and_collect_into_both():
    """``?environment=PAPER&environment=LIVE`` - OR within the category, one statement."""
    service, client = service_for(three_environments())
    page = _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER", "LIVE"]))

    assert ("in", "environment", ["PAPER", "LIVE"]) in predicates_of(client)
    assert [item["id"] for item in page["signals"]] == ["live-1", "paper-1"]
    assert "backtest-1" not in [item["id"] for item in page["signals"]]


def test_the_environment_predicate_is_sent_beside_the_ownership_predicate():
    """In the STATEMENT, not applied to rows already fetched - see this module's header."""
    service, client = service_for(three_environments())
    _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))

    predicates = predicates_of(client)
    assert ("eq", "user_id", OWNER["id"]) in predicates
    assert ("eq", "environment", "PAPER") in predicates


def test_the_filter_is_a_predicate_and_not_a_post_filter_over_the_page():
    """The proof: 120 LIVE signals and one PAPER one, page size 100.

    A post-filter over the first page would return NOTHING for ``?environment=PAPER`` - the
    PAPER signal is the 121st row by ``generated_at`` - while a predicate in the statement
    finds it on the first page.
    """
    rows = [
        signal_row(
            f"live-{index:03d}",
            environment="LIVE",
            generated_at=f"2024-05-02T{index // 60:02d}:{index % 60:02d}:00+00:00",
        )
        for index in range(120)
    ]
    rows.append(
        signal_row(
            "paper-late",
            environment="PAPER",
            paper_session_id="sess-1",
            generated_at="2024-04-01T00:00:00+00:00",
        )
    )
    service, _ = service_for(rows)

    page = _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))

    assert [item["id"] for item in page["signals"]] == ["paper-late"]
    assert page["count"] == 1
    assert page["has_more"] is False


def test_the_environment_filter_ands_with_another_category():
    service, _ = service_for(
        [
            signal_row("paper-btc", environment="PAPER", paper_session_id="s1"),
            signal_row("paper-eth", environment="PAPER", paper_session_id="s1", symbol="ETH/USDT"),
            signal_row("live-btc", environment="LIVE"),
        ]
    )

    page = _run_coroutine(
        service.list_signal_trace(OWNER, environment=["PAPER"], symbol=["BTC/USDT"])
    )

    assert [item["id"] for item in page["signals"]] == ["paper-btc"]
    assert {"environment", "symbol"} <= set(page["active_filters"])


def test_the_active_filter_list_names_the_environment_category():
    """Requirement 17.7's empty-state discriminator has to know this category exists."""
    service, _ = service_for(three_environments())
    page = _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))
    assert "environment" in page["active_filters"]
    assert page["filters_active"] is True
    assert page["environment_filter"] == ["PAPER"]


# ══════════════════════════════════════════════════════════════════════════
# 3. THE REFUSAL - refused, not ignored
# ══════════════════════════════════════════════════════════════════════════


def test_an_unknown_environment_is_refused_rather_than_matching_nothing():
    """Ignoring it would return every environment's signals - MORE than was asked for."""
    service, client = service_for(three_environments())

    with pytest.raises(SignalEnvironmentRefused) as refusal:
        _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPR"]))

    assert refusal.value.http_status == 400
    assert refusal.value.code == "SIGNAL_ENVIRONMENT_UNRECOGNISED"
    assert refusal.value.details["unrecognised"] == ["PAPR"]
    assert refusal.value.details["recognised_environments"] == ["BACKTEST", "PAPER", "LIVE"]
    assert client.queries == [], "the refusal happens before any statement is issued"


def test_one_bad_value_beside_a_good_one_still_refuses():
    """A partial answer to a filter is not an answer to it."""
    service, _ = service_for(three_environments())
    with pytest.raises(SignalEnvironmentRefused) as refusal:
        _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER", "SANDBOX"]))
    assert refusal.value.details["unrecognised"] == ["SANDBOX"]


def test_the_export_refuses_the_same_value_the_list_does():
    service, _ = service_for(three_environments())
    with pytest.raises(SignalEnvironmentRefused):
        _run_coroutine(service.export_signal_trace(OWNER, environment=["PAPR"]))


def test_the_detail_refuses_the_same_value_the_list_does():
    service, _ = service_for(three_environments())
    with pytest.raises(SignalEnvironmentRefused):
        _run_coroutine(service.get_signal_trace(OWNER, "live-1", environment=["PAPR"]))


# ══════════════════════════════════════════════════════════════════════════
# 4. THE LABEL (Requirement 23.4: no unlabelled PAPER+LIVE aggregate)
# ══════════════════════════════════════════════════════════════════════════


def test_every_item_carries_its_own_environment_and_session():
    item = signal_trace_item(
        signal_row("paper-1", environment="PAPER", paper_session_id="sess-1")
    )
    assert item["environment"] == "PAPER"
    assert item["paper_session_id"] == "sess-1"


def test_both_keys_are_present_even_on_a_pre_010_row():
    """Present with ``None``, so a reader branches on a value and never on a missing key."""
    row = signal_row("live-1")
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        row.pop(column)
    item = signal_trace_item(row)
    assert item["environment"] is None
    assert item["paper_session_id"] is None


def test_the_page_reports_a_count_per_environment():
    service, _ = service_for(three_environments())
    page = _run_coroutine(service.list_signal_trace(OWNER))

    assert page["count_by_environment"] == {"BACKTEST": 1, "PAPER": 1, "LIVE": 1}
    assert page["count"] == 3, "the mixed total is still there - it is now labelled beside it"


def test_the_per_environment_counts_are_ordered_by_the_check_constraints_own_order():
    counts = environment_counts(
        [{"environment": "LIVE"}, {"environment": "PAPER"}, {"environment": "BACKTEST"}]
    )
    assert list(counts) == ["BACKTEST", "PAPER", "LIVE"]


def test_an_unlabelled_row_is_counted_as_unlabelled_and_not_as_live():
    """Requirement 23.7 back-fills to LIVE in the MIGRATION; until then this cannot claim it."""
    assert environment_counts([{"environment": None}]) == {UNLABELLED_ENVIRONMENT: 1}
    assert environment_counts([{}]) == {UNLABELLED_ENVIRONMENT: 1}


def test_the_environment_source_says_where_the_label_came_from():
    service, _ = service_for(three_environments())
    page = _run_coroutine(service.list_signal_trace(OWNER))
    assert page["environment_source"] == ENVIRONMENT_SOURCE_COLUMN
    assert page["environment_degraded"] is None


def test_the_export_counts_every_page_and_not_only_the_first():
    """A per-environment figure covering 100 of 150 rows would be the mix, one level up."""
    rows = [
        signal_row(
            f"live-{index:03d}",
            environment="LIVE",
            generated_at=f"2024-05-02T{index // 60:02d}:{index % 60:02d}:00+00:00",
        )
        for index in range(120)
    ]
    rows += [
        signal_row(
            f"paper-{index:03d}",
            environment="PAPER",
            paper_session_id="sess-1",
            generated_at=f"2024-04-02T{index // 60:02d}:{index % 60:02d}:00+00:00",
        )
        for index in range(30)
    ]
    service, _ = service_for(rows)

    export = _run_coroutine(service.export_signal_trace(OWNER, format="json"))

    assert export["row_count"] == 150
    assert export["count_by_environment"] == {"PAPER": 30, "LIVE": 120}


def test_the_csv_carries_the_environment_and_the_applicable_identifier():
    service, _ = service_for(
        [signal_row("paper-1", environment="PAPER", paper_session_id="sess-1")]
    )
    export = _run_coroutine(service.export_signal_trace(OWNER, format="csv"))

    reader = list(csv.reader(io.StringIO(export["content"], newline="")))
    header, (line,) = reader[0], reader[1:]
    cell = dict(zip(header, line))

    assert header == [name for name, _path in SIGNAL_TRACE_EXPORT_COLUMNS]
    assert cell["environment"] == "PAPER"
    assert cell["paper_session_id"] == "sess-1"
    assert cell["deployment_id"] == ""  # a Paper_Session is not a deployment


# ══════════════════════════════════════════════════════════════════════════
# 5. THE PRE-010 DEGRADATION - it says so, on the response
# ══════════════════════════════════════════════════════════════════════════


def test_without_010_an_unfiltered_list_still_answers_every_signal():
    """Degrade, not refuse: an absent column costs the LABEL, not the list."""
    service, _ = service_for(three_environments(), missing_environment=True)

    page = _run_coroutine(service.list_signal_trace(OWNER))

    assert page["count"] == 3
    assert page["count_by_environment"] == {UNLABELLED_ENVIRONMENT: 3}
    assert page["environment_source"] == ENVIRONMENT_SOURCE_UNAVAILABLE
    assert page["environment_degraded"]["migration"] == SIGNAL_ENVIRONMENT_MIGRATION
    assert page["environment_degraded"]["environment_filter_answered"] is True


def test_without_010_a_filtered_list_is_empty_and_says_why():
    """Not the unfiltered list (a lie about what was filtered) and not a silent empty one."""
    service, client = service_for(three_environments(), missing_environment=True)

    page = _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))

    assert page["signals"] == []
    assert page["filters_active"] is True
    assert page["active_filters"] == ["environment"]
    degraded = page["environment_degraded"]
    assert degraded["migration"] == SIGNAL_ENVIRONMENT_MIGRATION
    assert degraded["environment_filter"] == ["PAPER"]
    assert degraded["environment_filter_answered"] is False
    assert page["environment_source"] == ENVIRONMENT_SOURCE_UNAVAILABLE
    # And no list statement was issued at all - only the probe, which carries no predicate.
    assert [q for q in client.queries if q.predicates] == []


def test_without_010_the_degradation_warning_names_the_migration_file(caplog):
    service, _ = service_for(three_environments(), missing_environment=True)
    with caplog.at_level("WARNING"):
        _run_coroutine(service.list_signal_trace(OWNER, environment=["PAPER"]))
    assert "010_signal_environment.sql" in caplog.text


def test_without_010_the_export_declares_the_same_thing_on_the_file():
    service, _ = service_for(three_environments(), missing_environment=True)

    export = _run_coroutine(
        service.export_signal_trace(OWNER, format="json", environment=["PAPER"])
    )

    assert export["row_count"] == 0
    body = json.loads(export["content"])
    assert body["environment_degraded"]["environment_filter_answered"] is False
    assert body["environment_degraded"]["migration"] == SIGNAL_ENVIRONMENT_MIGRATION
    assert body["environment_source"] == ENVIRONMENT_SOURCE_UNAVAILABLE


def test_without_010_the_detail_answers_404_and_names_the_migration_in_the_body():
    """One signal has no empty page to return, so the same statement arrives as the refusal."""
    service, client = service_for(three_environments(), missing_environment=True)

    with pytest.raises(SignalEnvironmentFilterUnanswerable) as refusal:
        _run_coroutine(service.get_signal_trace(OWNER, "paper-1", environment=["PAPER"]))

    assert refusal.value.http_status == 404
    assert refusal.value.code == "SIGNAL_NOT_FOUND"
    detail = refusal.value.to_detail()
    assert detail["environment_filter"] == ["PAPER"]
    assert detail["environment_filter_answered"] is False
    assert detail["migration"] == SIGNAL_ENVIRONMENT_MIGRATION
    # Raised before the row is read, so the answer cannot depend on whether it exists.
    assert [q for q in client.queries if q.predicates] == []


def test_without_010_the_unanswerable_detail_answers_identically_for_a_signal_that_exists_and_one_that_does_not():
    service, _ = service_for(three_environments(), missing_environment=True)

    bodies = []
    for signal_id in ("paper-1", "no-such-signal"):
        with pytest.raises(SignalEnvironmentFilterUnanswerable) as refusal:
            _run_coroutine(
                service.get_signal_trace(OWNER, signal_id, environment=["PAPER"])
            )
        bodies.append(refusal.value.to_detail())

    assert bodies[0] == bodies[1], (
        "the unanswerable-filter refusal differs between an existing signal and a "
        "nonexistent one, which makes it a channel for whether a signal exists"
    )


def test_without_010_the_detail_without_a_filter_still_answers_in_full():
    service, _ = service_for(three_environments(), missing_environment=True)
    detail = _run_coroutine(service.get_signal_trace(OWNER, "live-1"))
    assert detail is not None
    assert detail["signal"]["id"] == "live-1"
    assert detail["signal"]["environment"] is None
    assert set(detail) == {
        "signal",
        "trace",
        "lifecycle_transitions",
        "timeline",
        "lifecycle_state_source",
        "degraded",
    }


# ══════════════════════════════════════════════════════════════════════════
# 6. THE DEFAULT IS UNCHANGED (Requirement 23.6)
# ══════════════════════════════════════════════════════════════════════════


def test_no_filter_means_every_environment_scoped_by_the_authenticated_identity():
    service, client = service_for(
        three_environments() + [signal_row("someone-else", user_id="user-zzzz")]
    )

    page = _run_coroutine(service.list_signal_trace(OWNER))

    assert [item["id"] for item in page["signals"]] == ["live-1", "paper-1", "backtest-1"]
    assert page["filters_active"] is False
    assert page["environment_filter"] == []
    assert ("eq", "user_id", OWNER["id"]) in predicates_of(client)
    assert not [p for p in predicates_of(client) if p[1] == "environment"]


def test_a_non_owner_sees_none_of_the_owners_signals_in_any_environment():
    service, _ = service_for(three_environments())
    page = _run_coroutine(
        service.list_signal_trace({"id": "user-zzzz"}, environment=["PAPER", "LIVE"])
    )
    assert page["signals"] == []


def test_the_detail_filter_answers_a_wrong_environment_as_a_missing_signal():
    """Requirements 20.1, 20.2: the row is not returned, so there is nothing to answer with."""
    service, _ = service_for(three_environments())

    assert _run_coroutine(
        service.get_signal_trace(OWNER, "live-1", environment=["PAPER"])
    ) is None
    detail = _run_coroutine(service.get_signal_trace(OWNER, "live-1", environment=["LIVE"]))
    assert detail is not None and detail["signal"]["id"] == "live-1"


def test_the_detail_environment_predicate_is_in_the_statement():
    service, client = service_for(three_environments())
    _run_coroutine(service.get_signal_trace(OWNER, "paper-1", environment=["PAPER"]))

    reads = [
        q
        for q in client.queries
        if q.table_name == "signals" and ("eq", "id", "paper-1") in q.predicates
    ]
    assert reads, "the detail never read the signal"
    assert ("eq", "environment", "PAPER") in reads[0].predicates


def test_an_unfiltered_detail_read_adds_no_environment_predicate():
    """The statement an unfiltered detail issues is the one it always issued."""
    service, client = service_for(three_environments())
    _run_coroutine(service.get_signal_trace(OWNER, "live-1"))

    reads = [
        q
        for q in client.queries
        if q.table_name == "signals" and ("eq", "id", "live-1") in q.predicates
    ]
    assert reads
    assert not [p for p in reads[0].predicates if p[1] == "environment"]
