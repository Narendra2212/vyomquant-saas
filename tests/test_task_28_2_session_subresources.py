"""
tests/test_task_28_2_session_subresources.py - the seven Paper_Session sub-resource reads.

Spec: marketplace-subscriptions-paper-trading task 28.2. Requirements 17.2, 19.8, 19.9, 21.5, 27.2.

WHAT THIS MODULE ASSERTS
------------------------
``GET /api/paper/sessions/{id}/orders`` | ``/fills`` | ``/positions`` | ``/trades`` | ``/equity`` |
``/metrics`` | ``/events``, and only what the ROUTE layer contributes. The reads themselves are
``paper_repository``'s and are proved in ``tests/test_paper_repository.py``; the replay is
``paper_channel``'s and is proved in ``tests/test_task_26_4_paper_channel_delivery.py``. Neither is
re-proved here, and a test in this file that re-asserted one of them would be a second account of it.

The seven claims, each checkable against the requirement it names:

1. **The route table.** Seven GETs at 120/60s, each resolving to its own handler.
   ``tests/test_task_28_1_session_routes.py`` owns the assertion that the whole ``/api/paper``
   surface is the existing eight plus 28.1's seven plus these seven - :data:`SUBRESOURCE_ROUTES` is
   defined there and imported here so the two files cannot describe different route sets.
2. **The authenticated identity, on every one of them** (Requirement 21.1). Asserted against the
   route's resolved dependency graph rather than against the source, so a ``Depends`` that was
   deleted while the import stayed cannot pass. No route accepts an identity parameter.
3. **``_safe_uuid`` on the path parameter** - a malformed identifier is a 422 taken BEFORE any
   statement is issued.
4. **Both predicates on the statement, and exactly one statement for the rows** (Requirements 21.5,
   27.2). The double records what was issued: ``user_id`` and ``session_id`` are predicates on the
   sub-resource select, and the count of statements does not move when the row count does - which is
   the "no per-row round trip" clause, asserted rather than assumed.
5. **Another tenant's session answers byte-identically to an unknown one** (Requirements 21.4,
   22.9). Compared as response BYTES with only the per-request correlation id removed, and the
   sub-resource table is never even read.
6. **No response carries Protected_Logic** (Requirements 19.7, 23.3). The four withheld columns are
   absent from every projected row; a protected column injected into a stored row makes the response
   a refusal rather than a leak.
7. **A read that did not complete is a 503, never an empty list** (Requirement 28.3), and
   ``/metrics`` reports absent as absent rather than as zero (Requirement 28.5).

And for ``/events`` specifically: the 5000-row cap, the ``HISTORY_INCOMPLETE`` answer, paging by
``since_sequence``, and that the REST history is FRAME-FOR-FRAME the socket replay's for the same
cursor - the last of which is the whole point of the clause, because a client that cannot hold a
socket must not be served a different history.

THE DOUBLES
-----------
``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double - bound through
``PaperTradingService.bind_persistence``, and ``tests/test_task_28_1_session_routes``'s ``_client``
/ ``_release`` / ``_comparable`` harnesses, imported rather than re-written so both files exercise
the same wiring. Events are seeded with
``tests/test_task_26_4_paper_channel_delivery._seed_events``, whose row shape is checked against the
real ``insert_session_event`` in that file.

Coroutines are driven with ``tests/test_paper_order_lifecycle_writes._run_coroutine``, the process's
ONE event loop. ``asyncio.run`` appears nowhere in this file: a loop per call exhausted the machine's
ephemeral port range and hung the paper suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.paper import paper_channel as pc
from backend_app.backend.paper import paper_events as events
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper_trading_service import PAPER_EXECUTION_ENVIRONMENT
from backend_app.routers import paper_trading
from tests.test_paper_order_lifecycle_writes import _run_coroutine
from tests.test_paper_repository import FakeSupabase
from tests.test_task_26_4_paper_channel_delivery import _seed_events
from tests.test_task_28_1_session_routes import (
    NOW,
    OTHER_USER,
    SESSION,
    SUBRESOURCE_ROUTES,
    UNKNOWN_SESSION,
    USER,
    _client,
    _comparable,
    _decorator_sources,
    _release,
    _reset_rate_limit_counters,
    _session_row,
)

ACCOUNT = "88888888-8888-8888-8888-888888888888"
ORDER = "aaaaaaaa-0000-0000-0000-000000000001"
SIGNAL = "bbbbbbbb-0000-0000-0000-000000000001"
SYMBOL = "BTC/USDT"

#: What a caller must never see coming back out of a paper response, whatever put it in the row.
#: A distinctive string, so "it was not echoed" is checkable against the whole response body rather
#: than against the fields a reader thought to look at.
SMUGGLED = "rsi14lt30d0n0tsh0wthis"


# ══════════════════════════════════════════════════════════════════════════
# THE SEVEN SUB-RESOURCES, AS ONE TABLE
# ══════════════════════════════════════════════════════════════════════════


class Sub:
    """One sub-resource: its path segment, its table, its projection and how to seed a row.

    A table rather than seven near-identical test classes, because every claim in this file except
    the ``/events``-specific ones is the SAME claim about all seven - and seven copies of it are
    seven places one of them could quietly be dropped from.
    """

    def __init__(
        self,
        segment: str,
        *,
        handler: str,
        table: str,
        body_key: str,
        fields: Tuple[str, ...],
        row: Callable[..., Dict[str, Any]],
        collection: bool = True,
    ) -> None:
        self.segment = segment
        self.handler = handler
        self.table = table
        self.body_key = body_key
        self.fields = fields
        self.row = row
        #: ``False`` for ``/metrics``, which serves ONE row or ``None`` rather than a list.
        self.collection = collection

    def path(self, session_id: str = SESSION) -> str:
        return f"/api/paper/sessions/{session_id}/{self.segment}"

    def __repr__(self) -> str:  # pragma: no cover - test ids only
        return f"<Sub {self.segment}>"


def _order_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    """One ``paper_orders`` row as the table holds it - withheld columns included ON PURPOSE.

    ``user_id``, ``account_id`` and ``fingerprint`` are present because the double returns whole
    rows (PostgREST projects; an in-process dict cannot), so the only thing that can keep them out
    of a response is the route's own projection - and a fixture that omitted them would make every
    omission assertion in this file pass without a projection.

    Money and quantities are exact decimal STRINGS, which is how a PostgREST ``NUMERIC`` round trip
    presents them. Never floats (Requirement 18.1).
    """
    return {
        "id": f"{ORDER[:-1]}{index}",
        "session_id": session_id,
        "account_id": ACCOUNT,
        "user_id": user_id,
        "symbol": SYMBOL,
        "side": "buy",
        "order_type": "limit",
        "quantity": "0.50000000",
        "limit_price": "65000.00",
        "reference_price": "65010.00",
        "filled_quantity": "0.50000000",
        "avg_fill_price": "65000.00",
        "fee_minor": 3250,
        "slippage_minor": 0,
        "order_state": "FILLED",
        "legacy_status": "FILLED",
        "rejection_reason": None,
        "idempotency_key": f"idem-{index}",
        "signal_id": SIGNAL,
        "fingerprint": "sha256:0f0f0f",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


def _fill_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    return {
        "id": f"paper_fills-{index}",
        "order_id": ORDER,
        "session_id": session_id,
        "user_id": user_id,
        "fill_event_id": f"fill-{index}",
        "quantity": "0.25000000",
        "price": "65000.00",
        "fee_minor": 1625,
        "slippage_minor": 0,
        "market_event_id": f"candle-{index}",
        "filled_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


def _position_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    return {
        "id": f"paper_positions-{index}",
        "session_id": session_id,
        "account_id": ACCOUNT,
        "user_id": user_id,
        "symbol": f"{SYMBOL}-{index}",
        "side": "LONG",
        "size": "0.50000000",
        "entry_price": "65000.00",
        "current_price": "65500.00",
        "unrealized_pnl": "250.00",
        "price_at": NOW.isoformat(),
        "opened_at": NOW.isoformat(),
        "closed_at": None,
        # The optimistic-concurrency counter. Withheld by the route, which is the one column whose
        # NAME would otherwise put a "version field" in a response.
        "version": 4,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


def _trade_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    return {
        "id": f"paper_trades-{index}",
        "session_id": session_id,
        "account_id": ACCOUNT,
        "user_id": user_id,
        "symbol": SYMBOL,
        "side": "LONG",
        "quantity": "0.50000000",
        "entry_price": "65000.00",
        "exit_price": "65500.00",
        "realized_pnl": "250.00",
        "fee_minor": 3250,
        "opened_at": NOW.isoformat(),
        "closed_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


def _equity_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    return {
        "id": f"paper_equity_snapshots-{index}",
        "session_id": session_id,
        "user_id": user_id,
        "series_index": 0,
        "total_equity": "100250.00",
        "available_balance": "67750.00",
        "locked_balance": "0.00",
        "position_market_value": "32500.00",
        "stale": False,
        "cause": "FILL",
        "taken_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


def _metrics_row(
    *, session_id: str = SESSION, user_id: str = USER, index: int = 1, **extra: Any
) -> Dict[str, Any]:
    """One ``paper_metrics`` row. ``win_rate`` is ``None`` deliberately - see Requirement 18.10.

    A session with no closed trade has no win rate, and the row records that as ``NULL`` rather than
    as ``0``. :class:`TestMetricsReportsAbsentAsAbsent` is what holds the route to passing it
    through unflattened.
    """
    return {
        "id": f"paper_metrics-{index}",
        "session_id": session_id,
        "user_id": user_id,
        "total_return_pct": "0.25",
        "realized_pnl": "250.00",
        "unrealized_pnl": None,
        "max_drawdown_amount": "0.00",
        "max_drawdown_fraction": "0.00",
        "win_rate": None,
        "closed_trade_count": 0,
        "order_count": 1,
        "fill_count": 2,
        "computed_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **extra,
    }


#: The six row-bearing sub-resources. ``/events`` is deliberately NOT here: its rows are frames
#: rather than a column projection, and its own class asserts the frame-level claims.
SUBS: Tuple[Sub, ...] = (
    Sub(
        "orders",
        handler="get_paper_session_orders",
        table=repo.ORDERS_TABLE,
        body_key="orders",
        fields=paper_trading.ORDER_VIEW_FIELDS,
        row=_order_row,
    ),
    Sub(
        "fills",
        handler="get_paper_session_fills",
        table=repo.FILLS_TABLE,
        body_key="fills",
        fields=paper_trading.FILL_VIEW_FIELDS,
        row=_fill_row,
    ),
    Sub(
        "positions",
        handler="get_paper_session_positions",
        table=repo.POSITIONS_TABLE,
        body_key="positions",
        fields=paper_trading.POSITION_VIEW_FIELDS,
        row=_position_row,
    ),
    Sub(
        "trades",
        handler="get_paper_session_trades",
        table=repo.TRADES_TABLE,
        body_key="trades",
        fields=paper_trading.TRADE_VIEW_FIELDS,
        row=_trade_row,
    ),
    Sub(
        "equity",
        handler="get_paper_session_equity",
        table=repo.EQUITY_SNAPSHOTS_TABLE,
        body_key="equity",
        fields=paper_trading.EQUITY_VIEW_FIELDS,
        row=_equity_row,
    ),
    Sub(
        "metrics",
        handler="get_paper_session_metrics",
        table=repo.METRICS_TABLE,
        body_key="metrics",
        fields=paper_trading.METRICS_VIEW_FIELDS,
        row=_metrics_row,
        collection=False,
    ),
)

EVENTS = Sub(
    "events",
    handler="get_paper_session_events",
    table=repo.EVENTS_TABLE,
    body_key="events",
    fields=(),
    row=_order_row,  # unused: an event is a frame, not a column projection
)

ALL_SEGMENTS: Tuple[str, ...] = tuple(sub.segment for sub in SUBS) + (EVENTS.segment,)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES AND HELPERS
# ══════════════════════════════════════════════════════════════════════════


#: Which ``FakeSupabase`` attribute each table's rows live on, for the tables the constructor does
#: not take a keyword for. ``fills`` and ``events`` are append-only by construction there - see that
#: class's own note on why the two logs are not seedable through ``__init__``.
_APPEND_ONLY = {repo.FILLS_TABLE: "fills", repo.EVENTS_TABLE: "events"}

_CONSTRUCTOR_KEYWORD = {
    repo.ORDERS_TABLE: "orders",
    repo.POSITIONS_TABLE: "positions",
    repo.TRADES_TABLE: "trades",
    repo.EQUITY_SNAPSHOTS_TABLE: "equity_snapshots",
    repo.METRICS_TABLE: "metrics",
}


def _double(
    *sessions: Dict[str, Any],
    rows: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    **kwargs: Any,
) -> FakeSupabase:
    """A Persistence_Layer double holding ``sessions`` and, per table, ``rows``."""
    repo.reset_persistence_probe()
    seeded = dict(rows or {})
    constructor: Dict[str, Any] = {}
    for table, keyword in _CONSTRUCTOR_KEYWORD.items():
        if table in seeded:
            constructor[keyword] = [dict(row) for row in seeded[table]]
    client = FakeSupabase(
        sessions=[dict(row) for row in sessions] or [_session_row()],
        **constructor,
        **kwargs,
    )
    for table, attribute in _APPEND_ONLY.items():
        for row in seeded.get(table, []):
            getattr(client, attribute).append(dict(row))
    return client


def _seeded(sub: Sub, *, count: int = 1, **row_kwargs: Any) -> FakeSupabase:
    """A double holding one session and ``count`` rows of ``sub``'s table."""
    return _double(
        _session_row(),
        rows={
            sub.table: [
                sub.row(index=index + 1, **row_kwargs) for index in range(count)
            ]
        },
    )


@pytest.fixture(autouse=True)
def _isolated_router() -> Any:
    """Forget the migration verdict, the bound singleton, the auth override and the owner cache.

    The same reset ``tests/test_task_28_1_session_routes`` performs, plus ``paper_channel``'s
    module-scope owner cache, which ``/events`` reaches through the replay. Cleared on both sides:
    module-scope state is exactly what lets one test decide another's answer.
    """
    repo.reset_persistence_probe()
    pc.invalidate_session_owner()
    _reset_rate_limit_counters()
    yield
    _release()
    pc.invalidate_session_owner()
    _reset_rate_limit_counters()
    repo.reset_persistence_probe()


def _selects(client: FakeSupabase, table: str) -> List[Any]:
    return client.statements_on(table, "select")


def _issued(client: FakeSupabase) -> List[Tuple[str, str]]:
    """The statements a request issued, with the once-per-process migration probe excluded.

    ``paper_repository.require_persistence`` establishes that 009 is applied with one
    ``SELECT id FROM paper_accounts LIMIT 1`` and caches the verdict for the life of the process.
    :func:`_isolated_router` re-arms that verdict around every test so no test inherits another's,
    which puts exactly one probe statement in front of the first read of each test here.

    It is excluded from the round-trip assertions because it is not one: it is issued once per
    process in production, carries no predicate and reads no row of anybody's data. The exclusion is
    kept honest by
    :meth:`TestBothPredicatesAreOnTheStatement.test_the_only_paper_accounts_statement_is_the_probe`,
    which names it and asserts there is exactly one - so a route that started reading an account
    could not hide behind this filter.
    """
    return [
        (statement.op, statement.table_name)
        for statement in client.statements
        if statement.table_name != repo.ACCOUNTS_TABLE
    ]


def _body(response: Any) -> Dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def _rows(response: Any, sub: Sub) -> List[Dict[str, Any]]:
    return _body(response)[sub.body_key]


# ══════════════════════════════════════════════════════════════════════════
#  1. THE ROUTE TABLE
# ══════════════════════════════════════════════════════════════════════════


class TestTheRouteTable:
    """Seven GETs, each at 120/60s, each resolving to its own handler.

    Red run: ``@limiter.limit("120/minute")`` was changed to ``"600/minute"`` on ``/events`` and
    :meth:`test_each_route_carries_the_read_rate_limit` failed naming the handler; the ``/equity``
    path was spelled ``/equity-curve`` and both the registration test here and
    ``test_task_28_1_session_routes::test_no_route_was_added_beyond_the_seven`` failed.
    """

    @staticmethod
    def _registered() -> Dict[Tuple[str, str], str]:
        from backend_app.main import app

        table: Dict[Tuple[str, str], str] = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/paper"):
                continue
            for method in getattr(route, "methods", None) or []:
                if method in ("HEAD", "OPTIONS"):
                    continue
                table[(method, path)] = getattr(route, "name", "")
        return table

    def test_the_seven_sub_resource_routes_are_registered(self) -> None:
        registered = self._registered()
        for handler, (method, path, _limit) in SUBRESOURCE_ROUTES.items():
            assert (method, path) in registered, (
                f"{method} {path} is not registered; task 28.2 adds all seven sub-resource reads"
            )
            assert registered[(method, path)] == handler, (
                f"{method} {path} resolves to {registered[(method, path)]!r} rather than to its "
                f"own handler {handler!r} - a route registered ahead of it is shadowing it"
            )

    def test_the_table_covers_the_seven_segments_task_28_2_names(self) -> None:
        assert sorted(
            path.rsplit("/", 1)[-1] for _m, path, _l in SUBRESOURCE_ROUTES.values()
        ) == sorted(ALL_SEGMENTS)
        assert len(SUBRESOURCE_ROUTES) == 7

    @pytest.mark.parametrize("handler", sorted(SUBRESOURCE_ROUTES))
    def test_each_route_carries_the_read_rate_limit(self, handler: str) -> None:
        _method, _path, limit = SUBRESOURCE_ROUTES[handler]
        assert limit == "120/minute"
        decorators = _decorator_sources(handler)
        assert f'limiter.limit("{limit}")' in decorators, (
            f"{handler} does not carry task 28.2's {limit} rate limit; its decorators are "
            f"{decorators}"
        )

    @pytest.mark.parametrize("handler", sorted(SUBRESOURCE_ROUTES))
    def test_each_route_is_a_read(self, handler: str) -> None:
        """A GET, and nothing else: task 28.2 adds reads and writes nothing."""
        method, _path, _limit = SUBRESOURCE_ROUTES[handler]
        assert method == "GET"


# ══════════════════════════════════════════════════════════════════════════
#  2. THE AUTHENTICATED IDENTITY, ON EVERY ROUTE (Requirements 21.1, 21.6)
# ══════════════════════════════════════════════════════════════════════════


class TestEveryRouteCarriesTheAuthenticatedIdentity:
    """``get_current_user`` on all seven, asserted against the resolved dependency graph.

    Not against the source: a ``Depends`` that was deleted while the import stayed would still read
    correctly in the file. FastAPI's ``dependant`` tree is what it will actually call.

    Red run: ``user: dict = Depends(get_current_user)`` was removed from
    ``get_paper_session_orders`` and replaced by a ``user_id`` query parameter. This class failed
    twice - once on the dependency and once on
    :meth:`test_no_route_accepts_an_identity_parameter` - and every predicate assertion in
    :class:`TestBothPredicatesAreOnTheStatement` failed too, because the identity was then a client
    value.
    """

    @staticmethod
    def _dependencies(route: Any) -> List[Any]:
        collected: List[Any] = []
        pending = list(getattr(route.dependant, "dependencies", []))
        while pending:
            dependency = pending.pop()
            collected.append(getattr(dependency, "call", None))
            pending.extend(getattr(dependency, "dependencies", []))
        return collected

    @pytest.mark.parametrize("handler", sorted(SUBRESOURCE_ROUTES))
    def test_the_route_depends_on_get_current_user(self, handler: str) -> None:
        from backend_app.core.dependencies import get_current_user
        from backend_app.main import app

        route = next(r for r in app.routes if getattr(r, "name", "") == handler)
        assert get_current_user in self._dependencies(route), (
            f"{handler} does not depend on get_current_user; every network-exposed paper route "
            f"carries the authenticated identity (Requirements 21.1, 21.6, 22.1)"
        )

    def test_no_sub_resource_route_accepts_an_identity_parameter(self) -> None:
        """The only path parameter is ``session_id``, which is a reference and not an identity."""
        from backend_app.main import app

        forbidden = {"user_id", "owner_id", "tenant_id", "author_id", "version_id"}
        offenders: Dict[str, List[str]] = {}
        for handler in SUBRESOURCE_ROUTES:
            route = next(r for r in app.routes if getattr(r, "name", "") == handler)
            names = {
                field.name
                for field in list(route.dependant.query_params)
                + list(route.dependant.path_params)
            }
            named = sorted(names & forbidden)
            if named:
                offenders[handler] = named
        assert offenders == {}, (
            f"a sub-resource route accepts an identity from the request: {offenders}; no "
            f"identifier from a query or path may participate in authorisation (Requirement 21.1)"
        )

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_the_read_is_scoped_by_the_identity_the_dependency_produced(
        self, segment: str
    ) -> None:
        """Not by anything in the request: the caller is whoever ``get_current_user`` said.

        Driven by authenticating as a SECOND user against a session owned by the first: the scoped
        gate read answers nothing, so the response is the refusal rather than the first user's rows.
        """
        supabase = _double(_session_row(user_id=USER))
        client = _client(supabase, user_id=OTHER_USER)

        response = client.get(f"/api/paper/sessions/{SESSION}/{segment}")

        assert response.status_code == 404, response.text
        gate = _selects(supabase, repo.SESSIONS_TABLE)[0]
        assert gate.filter_value("user_id") == OTHER_USER


# ══════════════════════════════════════════════════════════════════════════
#  3. _safe_uuid ON THE PATH PARAMETER (Requirement 22.2)
# ══════════════════════════════════════════════════════════════════════════


class TestSafeUuidOnThePathParameter:
    """A malformed identifier is a 422 taken before any statement is issued.

    Red run: ``sid = _safe_uuid(session_id, "session_id")`` in ``_owned_session`` was replaced with
    ``sid = str(session_id)``. Every case here failed with a 404 instead of a 422, and the double
    had recorded a select - a malformed identifier had reached the Persistence_Layer.
    """

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_a_malformed_session_id_is_a_422(self, segment: str) -> None:
        supabase = _double(_session_row())
        client = _client(supabase)

        response = client.get(f"/api/paper/sessions/not-a-uuid/{segment}")

        assert response.status_code == 422, response.text
        assert "session_id" in response.text

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_no_statement_is_issued_for_a_malformed_session_id(
        self, segment: str
    ) -> None:
        supabase = _double(_session_row())
        client = _client(supabase)

        client.get(f"/api/paper/sessions/{SMUGGLED}/{segment}")

        assert supabase.statements == [], (
            "a malformed identifier reached the Persistence_Layer; Requirement 22.2 requires the "
            "identifier format to be validated BEFORE any database access"
        )


# ══════════════════════════════════════════════════════════════════════════
#  4. BOTH PREDICATES, ON THE STATEMENT (Requirements 21.5, 27.2)
# ══════════════════════════════════════════════════════════════════════════


class TestBothPredicatesAreOnTheStatement:
    """Scoped in the query by ``user_id`` AND ``session_id``, never filtered after retrieval.

    Red run: ``get_orders(supabase, caller_id, session_id=sid)`` was changed to
    ``get_orders(supabase, caller_id)`` with the session filtered out of the result in the handler.
    :meth:`test_the_session_is_a_predicate_on_the_statement` failed - the statement had no
    ``session_id`` predicate at all, which is the fetch-then-drop Requirement 21.5 forbids - and
    :meth:`test_a_row_of_another_session_is_never_fetched` failed with the other session's row in
    the response.
    """

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_caller_is_a_predicate_on_the_statement(self, sub: Sub) -> None:
        supabase = _seeded(sub)
        client = _client(supabase)

        _body(client.get(sub.path()))

        read = _selects(supabase, sub.table)[0]
        assert read.filter_value("user_id") == USER

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_session_is_a_predicate_on_the_statement(self, sub: Sub) -> None:
        supabase = _seeded(sub)
        client = _client(supabase)

        _body(client.get(sub.path()))

        read = _selects(supabase, sub.table)[0]
        assert read.filter_value("session_id") == SESSION

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_exactly_one_select_serves_the_sub_resource(self, sub: Sub) -> None:
        """One statement for the rows, plus the ownership gate. Nothing else."""
        supabase = _seeded(sub, count=3)
        client = _client(supabase)

        _body(client.get(sub.path()))

        assert len(_selects(supabase, sub.table)) == 1, (
            f"{sub.segment} issued {len(_selects(supabase, sub.table))} statements against "
            f"{sub.table}; task 28.2 requires ONE select per sub-resource"
        )
        assert len(_selects(supabase, repo.SESSIONS_TABLE)) == 1
        assert _issued(supabase) == [
            ("select", repo.SESSIONS_TABLE),
            ("select", sub.table),
        ], _issued(supabase)

    def test_the_only_paper_accounts_statement_is_the_probe(self) -> None:
        """What :func:`_issued` filters out, named: one predicate-free probe, and no account read."""
        sub = SUBS[0]
        supabase = _seeded(sub)
        client = _client(supabase)

        _body(client.get(sub.path()))

        probes = supabase.statements_on(repo.ACCOUNTS_TABLE)
        assert len(probes) == 1, [(p.op, p.filters) for p in probes]
        assert probes[0].op == "select"
        assert probes[0].filters == [], (
            "the paper_accounts statement carries a predicate, so it is a READ of an account "
            "rather than the migration probe _issued() excludes"
        )

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_statement_count_does_not_move_with_the_row_count(
        self, sub: Sub
    ) -> None:
        """Requirement 27.2, and the "no per-row round trip" clause read literally.

        A loop issuing a statement per order is what the clause forbids, so the assertion is that
        the number of statements is the SAME for one row and for twenty-five - which is a fact a
        per-row read cannot have.
        """
        one = _seeded(sub, count=1)
        _body(_client(one).get(sub.path()))
        _release()

        many = _seeded(sub, count=25)
        _body(_client(many).get(sub.path()))

        assert _issued(one) == _issued(many)

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_a_row_of_another_session_is_never_fetched(self, sub: Sub) -> None:
        """The session predicate, from the other end: a sibling session's row does not arrive."""
        supabase = _double(
            _session_row(),
            rows={
                sub.table: [
                    sub.row(index=1),
                    sub.row(index=2, session_id=UNKNOWN_SESSION),
                ]
            },
        )
        client = _client(supabase)

        body = _body(client.get(sub.path()))

        served = body[sub.body_key]
        rows = served if sub.collection else [served]
        assert rows and all(row["session_id"] == SESSION for row in rows), rows

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_another_tenants_row_is_never_fetched(self, sub: Sub) -> None:
        """Even inside the caller's own session id: ``user_id`` is a predicate regardless."""
        supabase = _double(
            _session_row(),
            rows={sub.table: [sub.row(index=1, user_id=OTHER_USER)]},
        )
        client = _client(supabase)

        body = _body(client.get(sub.path()))

        served = body[sub.body_key]
        assert served == ([] if sub.collection else None), served
        assert OTHER_USER not in body_text(body)


def body_text(body: Dict[str, Any]) -> str:
    """One body as the characters a client receives, for a substring assertion."""
    return json.dumps(body, sort_keys=True, default=str)


# ══════════════════════════════════════════════════════════════════════════
#  5. ANOTHER TENANT'S SESSION ANSWERS AS AN UNKNOWN ONE (Req 21.4, 22.9)
# ══════════════════════════════════════════════════════════════════════════


class TestAnotherTenantsSessionAnswersAsAnUnknownOne:
    """The same session id, two doubles, one byte-identical refusal - on all seven.

    The comparison is over the SAME identifier deliberately: the refusal echoes the id the caller
    supplied, so comparing two different ids would permit a difference that says nothing. Holding
    the id fixed and varying only whether the session exists for somebody else makes the response
    bytes the whole of the claim.

    Red run: ``_owned_session``'s ``row is None`` branch was changed to answer 403 ``FORBIDDEN``
    when a second unscoped read found the row. Every case here failed on both the status and the
    code, which is the disclosure that another tenant's session exists.
    """

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_the_refusal_is_byte_identical(self, segment: str) -> None:
        foreign_double = _double(_session_row(user_id=OTHER_USER))
        foreign = _client(foreign_double).get(f"/api/paper/sessions/{SESSION}/{segment}")
        _release()
        absent = _client(_double(_session_row(session_id=UNKNOWN_SESSION))).get(
            f"/api/paper/sessions/{SESSION}/{segment}"
        )

        assert foreign.status_code == absent.status_code == 404, foreign.text
        assert _comparable(foreign) == _comparable(absent), (
            f"/{segment} answers another tenant's session differently from an unknown one "
            f"(Requirements 21.4, 22.9)"
        )
        assert foreign.json()["error"]["details"] == {"session_id": SESSION}

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_the_sub_resource_table_is_never_read(self, segment: str) -> None:
        """The refusal happens at the gate, so the child table is not even reached."""
        supabase = _double(_session_row(user_id=OTHER_USER))
        client = _client(supabase)

        client.get(f"/api/paper/sessions/{SESSION}/{segment}")

        touched = {table for _op, table in _issued(supabase)}
        assert touched == {repo.SESSIONS_TABLE}, touched

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_refusal_discloses_nothing_about_the_session(self, sub: Sub) -> None:
        supabase = _double(
            _session_row(user_id=OTHER_USER),
            rows={sub.table: [sub.row(index=1, user_id=OTHER_USER)]},
        )
        response = _client(supabase).get(sub.path())

        assert response.status_code == 404
        assert OTHER_USER not in response.text
        for disclosure in ("RUNNING", "session_state", "owner", "author", ACCOUNT):
            assert disclosure not in response.text, (
                f"the refusal discloses {disclosure!r}, which tells a prober the session exists"
            )


# ══════════════════════════════════════════════════════════════════════════
#  6. NO PROTECTED FIELD REACHES A CLIENT (Requirements 19.7, 23.3)
# ══════════════════════════════════════════════════════════════════════════


class TestNoProtectedFieldReachesAClient:
    """The projection, the withheld columns, and the boundary assertion behind them.

    Red run: ``_row_view`` was changed to ``dict(row)``. :meth:`test_the_withheld_columns_are_absent`
    failed on all six with ``user_id``, ``account_id``, ``version`` and ``fingerprint`` in the body,
    and :meth:`test_a_protected_column_in_a_stored_row_is_refused_not_served` failed with the
    strategy logic echoed straight out of the row.
    """

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_withheld_columns_are_absent(self, sub: Sub) -> None:
        supabase = _seeded(sub)
        client = _client(supabase)

        served = _body(client.get(sub.path()))[sub.body_key]
        rows = served if sub.collection else [served]

        assert rows, "the fixture served no row, so this assertion would be vacuous"
        for row in rows:
            for column in sorted(paper_trading.ROW_WITHHELD_COLUMNS):
                assert column not in row, (
                    f"the {sub.segment} row carries {column!r}; it is withheld by "
                    f"ROW_WITHHELD_COLUMNS (Requirements 21.1, 22.9)"
                )

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_view_is_derived_from_the_repositorys_own_projection(
        self, sub: Sub
    ) -> None:
        """So a response cannot carry a column its statement did not select, and vice versa."""
        select = {
            repo.ORDERS_TABLE: repo.ORDER_SELECT,
            repo.FILLS_TABLE: repo.FILL_SELECT,
            repo.POSITIONS_TABLE: repo.POSITION_SELECT,
            repo.TRADES_TABLE: repo.TRADE_SELECT,
            repo.EQUITY_SNAPSHOTS_TABLE: repo.EQUITY_SNAPSHOT_SELECT,
            repo.METRICS_TABLE: repo.METRICS_SELECT,
        }[sub.table]
        columns = [c.strip() for c in select.split(",") if c.strip()]

        assert list(sub.fields) == [
            c for c in columns if c not in paper_trading.ROW_WITHHELD_COLUMNS
        ]
        assert set(sub.fields) <= set(columns)

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_the_served_keys_are_exactly_the_view(self, sub: Sub) -> None:
        supabase = _seeded(sub)
        client = _client(supabase)

        served = _body(client.get(sub.path()))[sub.body_key]
        rows = served if sub.collection else [served]

        for row in rows:
            assert list(row) == list(sub.fields)

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_a_protected_column_in_a_stored_row_is_refused_not_served(
        self, sub: Sub
    ) -> None:
        """Asserted at the boundary rather than trusted from the writer.

        A stored row that has grown a ``compiled_plan`` column - because a projection widened, or
        because something wrote one - is a response this layer REFUSES rather than one it strips: a
        silently stripped field is a response that no longer matches what a Paper_Channel subscriber
        receives, and a projection that has started dropping columns is a defect an operator needs
        to see.
        """
        supabase = _seeded(sub, compiled_plan={"buy_logic": SMUGGLED})
        # The row is served through the repository's projection, so the boundary check only fires
        # when the view itself carries the column. Widen the view the way a drifting projection
        # would, which is exactly the condition the assertion exists for.
        monkey = list(sub.fields) + ["compiled_plan"]
        original = sub.fields
        try:
            _install_view(sub, tuple(monkey))
            response = _client(supabase).get(sub.path())
        finally:
            _install_view(sub, original)

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "PAPER_READ_FAILED"
        assert SMUGGLED not in response.text, (
            "the refusal echoed the Protected_Logic it was refusing to serve"
        )

    def test_the_token_list_does_not_ban_the_schema_version_field(self) -> None:
        """Requirement 19.3 REQUIRES a schema version on every event; 19.7 bans a STRATEGY one."""
        assert paper_trading._protected_keys({"schema_version": "1.0"}) == []
        assert paper_trading._protected_keys({"version_id": "v"}) == ["version_id"]

    def test_the_boundary_check_reaches_a_nested_mapping(self) -> None:
        """``paper_session_stopped`` carries ``final_metrics`` as a mapping; depth is not a hiding place."""
        nested = {"payload": {"final_metrics": {"compiled_plan": {"a": 1}}}}
        assert paper_trading._protected_keys(nested) == ["compiled_plan"]
        assert paper_trading._protected_keys([{"nodes": []}]) == ["nodes"]

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_no_response_carries_a_strategy_token_anywhere(self, segment: str) -> None:
        """Over the served bytes, not over a field list somebody remembered to check."""
        supabase = _double(_session_row())
        for sub in SUBS:
            for row in [sub.row(index=1)]:
                attribute = _APPEND_ONLY.get(sub.table)
                if attribute is not None:
                    getattr(supabase, attribute).append(row)
                else:
                    getattr(supabase, FakeSupabase.TABLES[sub.table]).append(row)
        _seed_events(supabase, 2)
        client = _client(supabase)

        response = client.get(f"/api/paper/sessions/{SESSION}/{segment}")
        text = response.text.lower()

        assert response.status_code == 200, response.text
        for token in paper_trading.PROTECTED_FIELD_TOKENS:
            assert f'"{token}' not in text, (
                f"the /{segment} response carries a field naming {token!r}"
            )


def _install_view(sub: Sub, fields: Tuple[str, ...]) -> None:
    """Point both the table entry and the router's constant at ``fields``.

    Used by exactly one test, to reproduce a projection that has drifted wider than the four
    withheld columns allow. The router reads its constant at request time, so replacing it is what
    puts the boundary assertion in front of a column it would otherwise never see.
    """
    attribute = {
        repo.ORDERS_TABLE: "ORDER_VIEW_FIELDS",
        repo.FILLS_TABLE: "FILL_VIEW_FIELDS",
        repo.POSITIONS_TABLE: "POSITION_VIEW_FIELDS",
        repo.TRADES_TABLE: "TRADE_VIEW_FIELDS",
        repo.EQUITY_SNAPSHOTS_TABLE: "EQUITY_VIEW_FIELDS",
        repo.METRICS_TABLE: "METRICS_VIEW_FIELDS",
    }[sub.table]
    setattr(paper_trading, attribute, fields)
    sub.fields = fields


# ══════════════════════════════════════════════════════════════════════════
#  7. A FAILED READ IS NOT AN EMPTY LIST (Requirement 28.3)
# ══════════════════════════════════════════════════════════════════════════


class TestAFailedReadIsNotAnEmptyList:
    """503, and no collection key at all - never "you have none".

    Red run: ``_sub_resource``'s ``except paper_repo.PaperRepositoryError`` branch was changed to
    ``return []``. Every case here failed with a 200 and an empty list, which is the answer that
    tells a trader their session history is gone and lets a client draw an equity curve from a set
    it never received.
    """

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_a_statement_that_did_not_complete_is_a_503(self, sub: Sub) -> None:
        supabase = _seeded(sub)
        supabase.raise_on.add(("select", sub.table))
        client = _client(supabase)

        response = client.get(sub.path())

        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "PAPER_READ_FAILED"
        assert sub.body_key not in body

    def test_a_failed_events_read_is_a_503_rather_than_no_history(self) -> None:
        supabase = _double(_session_row())
        supabase.sessions[0]["event_sequence"] = 3
        _seed_events(supabase, 3)
        supabase.raise_on.add(("select", repo.EVENTS_TABLE))
        client = _client(supabase)

        response = client.get(EVENTS.path())

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "PAPER_READ_FAILED"
        assert "events" not in response.json()

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_a_failed_gate_read_is_a_503_rather_than_a_404(self, segment: str) -> None:
        """"The read did not complete" is not "no such session" (Requirements 17.2, 28.3)."""
        supabase = _double(_session_row())
        supabase.raise_on.add(("select", repo.SESSIONS_TABLE))
        client = _client(supabase)

        response = client.get(f"/api/paper/sessions/{SESSION}/{segment}")

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "PAPER_READ_FAILED"

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_an_empty_set_is_reported_as_empty_and_not_as_a_failure(
        self, sub: Sub
    ) -> None:
        """The other half: a completed read of nothing is a 200, not a 503."""
        supabase = _double(_session_row())
        client = _client(supabase)

        body = _body(client.get(sub.path()))

        assert body[sub.body_key] == ([] if sub.collection else None)


# ══════════════════════════════════════════════════════════════════════════
#  8. THE ENVELOPE (Requirements 13.6, 28.1)
# ══════════════════════════════════════════════════════════════════════════


class TestTheEnvelope:
    """Every body labels itself simulated, and reports the session it is about."""

    @pytest.mark.parametrize("segment", ALL_SEGMENTS)
    def test_the_body_carries_the_environment_and_the_simulated_marker(
        self, segment: str
    ) -> None:
        supabase = _double(_session_row())
        client = _client(supabase)

        body = _body(client.get(f"/api/paper/sessions/{SESSION}/{segment}"))

        assert body["execution_environment"] == PAPER_EXECUTION_ENVIRONMENT
        assert body["is_simulated"] is True
        assert body["session_id"] == SESSION

    @pytest.mark.parametrize("sub", SUBS[:-1], ids=lambda s: s.segment)
    def test_a_collection_reports_its_own_count(self, sub: Sub) -> None:
        supabase = _seeded(sub, count=4)
        client = _client(supabase)

        body = _body(client.get(sub.path()))

        assert body["count"] == len(body[sub.body_key]) == 4

    @pytest.mark.parametrize("sub", SUBS, ids=lambda s: s.segment)
    def test_no_money_figure_arrives_as_a_binary_float(self, sub: Sub) -> None:
        """An exact figure that arrives inexact is worse than one that does not arrive."""
        supabase = _seeded(sub)
        client = _client(supabase)

        floats = _floats(_body(client.get(sub.path())))

        assert floats == [], f"the {sub.segment} body carries float(s) {floats}"


def _floats(value: Any, path: str = "") -> List[str]:
    """Every path in ``value`` whose leaf is a ``float``. Exact figures travel as strings or ints."""
    found: List[str] = []
    if isinstance(value, bool):
        return found
    if isinstance(value, float):
        return [f"{path}={value!r}"]
    if isinstance(value, dict):
        for key, member in value.items():
            found.extend(_floats(member, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, member in enumerate(value):
            found.extend(_floats(member, f"{path}[{index}]"))
    return found


# ══════════════════════════════════════════════════════════════════════════
#  9. /metrics REPORTS ABSENT AS ABSENT (Requirement 28.5)
# ══════════════════════════════════════════════════════════════════════════


class TestMetricsReportsAbsentAsAbsent:
    """``None`` means "not computed", which is not "zero".

    Red run: the handler was changed to ``metrics or {}`` and then to a body carrying
    ``total_return_pct: 0``. Both cases failed here: the first on ``metrics is None``, the second on
    the zero, which is a figure presented as a measurement of a session that computed nothing
    (Requirement 28.5).
    """

    def test_no_metrics_row_is_reported_as_absent(self) -> None:
        supabase = _double(_session_row())
        client = _client(supabase)

        body = _body(client.get("/api/paper/sessions/%s/metrics" % SESSION))

        assert body["metrics"] is None
        assert body["computed"] is False

    def test_an_absent_row_is_a_200_and_not_a_404(self) -> None:
        """The session exists; its metrics have not been computed. Two different statements."""
        supabase = _double(_session_row())
        response = _client(supabase).get(f"/api/paper/sessions/{SESSION}/metrics")

        assert response.status_code == 200, response.text

    def test_no_figure_is_substituted_for_an_absent_one(self) -> None:
        supabase = _double(_session_row())
        body = _body(_client(supabase).get(f"/api/paper/sessions/{SESSION}/metrics"))

        assert "0" not in json.dumps(body["metrics"]), body
        assert body["metrics"] is None

    def test_an_absent_win_rate_stays_absent_inside_a_computed_row(self) -> None:
        """Requirement 18.10: the win rate is absent, not zero, while no trade has closed."""
        supabase = _seeded(SUBS[-1])
        body = _body(_client(supabase).get(f"/api/paper/sessions/{SESSION}/metrics"))

        assert body["computed"] is True
        assert body["metrics"]["win_rate"] is None, (
            "an absent win rate was flattened into a figure; Requirement 18.10 reports it as "
            "absent while the closed-trade count is zero"
        )
        assert body["metrics"]["unrealized_pnl"] is None
        assert body["metrics"]["closed_trade_count"] == 0

    def test_the_newest_computed_row_is_the_one_served(self) -> None:
        """009 places no unique index on ``paper_metrics.session_id``; the newest is current."""
        older = _metrics_row(index=1, computed_at="2024-01-01T00:00:00+00:00")
        newer = _metrics_row(index=2, computed_at="2025-01-01T00:00:00+00:00")
        supabase = _double(_session_row(), rows={repo.METRICS_TABLE: [older, newer]})

        body = _body(_client(supabase).get(f"/api/paper/sessions/{SESSION}/metrics"))

        assert body["metrics"]["computed_at"] == "2025-01-01T00:00:00+00:00"


# ══════════════════════════════════════════════════════════════════════════
# 10. /events IS THE WEBSOCKET REPLAY (Requirements 19.8, 19.9)
# ══════════════════════════════════════════════════════════════════════════


def _socket_replay(supabase: Any, *, since: int, session_id: str = SESSION) -> Any:
    """What a Paper_Channel subscriber's replay produces for the same cursor.

    ``paper_channel.replay`` - the module-level form, on the process registry, which is the same
    function ``api_ws`` reaches through ``paper_events.replay``. Called here so the comparison is
    against the socket's ACTUAL answer rather than against a transcription of it.
    """
    return pc.replay(
        supabase, session_id=session_id, user={"id": USER}, last_sequence=since
    )


def _canonical(frames: Any) -> str:
    return json.dumps(frames, sort_keys=True, default=str)


#: The three members of a ``paper_error`` frame that are per-EMISSION rather than per-answer:
#: ``error_frame`` mints a fresh ``event_id`` and stamps the moment it was built (``emitted_at``, and
#: ``payload.at``, which is the same instant rendered inside the payload). Two emissions of the same
#: refusal therefore differ in exactly these three and in nothing else, which is why they are dropped
#: from the comparison - the same reason ``_comparable`` drops ``request_id``. Everything that
#: constitutes the ANSWER - the channel, the session, the type, the sequence, the schema version, the
#: code, the sentence and ``recoverable`` - is compared.
_PER_EMISSION_MEMBERS = ("event_id", "emitted_at")


def _canonical_refusal(frame: Optional[Dict[str, Any]]) -> str:
    """One ``paper_error`` frame with its per-emission members removed, as canonical JSON."""
    if frame is None:
        return "null"
    body = {k: v for k, v in dict(frame).items() if k not in _PER_EMISSION_MEMBERS}
    payload = dict(body.get("payload") or {})
    payload.pop("at", None)
    body["payload"] = payload
    return _canonical(body)


class TestTheEventsHistoryIsTheSocketReplays:
    """Frame for frame, cap for cap, refusal for refusal.

    The comparison is against ``paper_channel.replay``'s own return value for the same cursor and
    the same stored log, which is the strongest form the claim has: the two transports are not
    "kept in step", they are one function. A REST endpoint that reimplemented the read would fail
    every case here the moment the two drifted.

    Red run: the handler was changed to call ``paper_repository.read_session_events`` directly and
    to build its own frames. :meth:`test_the_frames_are_the_socket_replays` failed on the frame
    shape, :meth:`test_a_negative_cursor_is_the_history_incomplete_answer` failed with a 500 from
    the repository's ``ValueError`` instead of the gap report, and
    :meth:`test_the_cap_is_the_sockets_5000` failed because the cap had become the caller's.
    """

    @staticmethod
    def _double(count: int, *, sequence: Optional[int] = None) -> FakeSupabase:
        supabase = _double(
            _session_row(),
        )
        supabase.sessions[0]["event_sequence"] = count if sequence is None else sequence
        _seed_events(supabase, count)
        return supabase

    @pytest.mark.parametrize("since", [0, 1, 3, 6])
    def test_the_frames_are_the_socket_replays(self, since: int) -> None:
        supabase = self._double(6)
        client = _client(supabase)

        body = _body(client.get(f"{EVENTS.path()}?since_sequence={since}"))
        expected = _socket_replay(supabase, since=since)

        assert _canonical(body["events"]) == _canonical(list(expected.frames)), (
            f"the REST history for since_sequence={since} is not the socket replay's; a client "
            f"that cannot hold a socket is being served a different history"
        )
        assert body["count"] == len(expected.frames)
        assert body["current_sequence"] == expected.current_sequence
        assert body["history_incomplete"] is expected.history_incomplete
        assert body["truncated"] is expected.truncated

    def test_the_whole_log_is_replayed_from_zero(self) -> None:
        supabase = self._double(6)
        body = _body(_client(supabase).get(f"{EVENTS.path()}?since_sequence=0"))

        assert [frame["sequence"] for frame in body["events"]] == [1, 2, 3, 4, 5, 6]

    def test_the_default_cursor_is_zero(self) -> None:
        supabase = self._double(4)
        body = _body(_client(supabase).get(EVENTS.path()))

        assert body["since_sequence"] == 0
        assert [frame["sequence"] for frame in body["events"]] == [1, 2, 3, 4]

    def test_paging_by_since_sequence_walks_the_log_once(self) -> None:
        """Each page starts above the last sequence applied, and no frame is served twice."""
        supabase = self._double(9)
        client = _client(supabase)

        seen: List[int] = []
        cursor = 0
        for _page in range(3):
            body = _body(
                client.get(f"{EVENTS.path()}?since_sequence={cursor}")
            )
            page = [frame["sequence"] for frame in body["events"]]
            if not page:
                break
            assert min(page) == cursor + 1
            seen.extend(page)
            cursor = page[-1]

        assert seen == list(range(1, 10))
        assert len(seen) == len(set(seen))

    def test_a_frame_carries_the_same_eight_fields_a_live_one_does(self) -> None:
        """Requirement 19.8's client-side dedupe needs one frame shape, not two."""
        supabase = self._double(1)
        body = _body(_client(supabase).get(EVENTS.path()))

        frame = body["events"][0]
        assert set(frame) == {
            "schema_version",
            "channel",
            "session_id",
            "type",
            "sequence",
            "event_id",
            "emitted_at",
            "payload",
        }
        assert frame["schema_version"] == events.PAPER_EVENT_SCHEMA_VERSION

    def test_no_exact_figure_in_a_frame_arrives_as_a_float(self) -> None:
        supabase = self._double(3)
        floats = _floats(_body(_client(supabase).get(EVENTS.path())))

        assert floats == [], floats

    def test_the_cap_is_the_sockets_5000(self) -> None:
        """One number, owned by the statement's ``limit``, reported on the body."""
        supabase = self._double(repo.SESSION_EVENT_REPLAY_CAP + 1)
        client = _client(supabase)

        body = _body(client.get(f"{EVENTS.path()}?since_sequence=0"))
        expected = _socket_replay(supabase, since=0)

        assert repo.SESSION_EVENT_REPLAY_CAP == 5000
        assert body["replay_cap"] == pc.REPLAY_ROW_CAP == 5000
        assert body["count"] == len(expected.frames) == 5000
        assert body["events"][-1]["sequence"] == 5000
        assert body["truncated"] is expected.truncated is True, (
            "the cap was reached with more above the last frame and the body did not say so"
        )

    def test_a_caller_cannot_ask_for_more_than_the_cap(self) -> None:
        """There is no limit parameter to raise: the cap has one owner (Requirement 19.8)."""
        from backend_app.main import app

        route = next(
            r for r in app.routes if getattr(r, "name", "") == EVENTS.handler
        )
        names = {field.name for field in route.dependant.query_params}
        assert names == {"since_sequence"}, names

    @pytest.mark.parametrize("since", [-1, -5000])
    def test_a_negative_cursor_is_the_history_incomplete_answer(
        self, since: int
    ) -> None:
        """Requirement 19.9's unrecoverable gap: a sequence starts at 1, so this names no position."""
        supabase = self._double(6)
        client = _client(supabase)

        response = client.get(f"{EVENTS.path()}?since_sequence={since}")
        body = _body(response)
        expected = _socket_replay(supabase, since=since)

        assert body["history_incomplete"] is True
        assert body["events"] == [], (
            "Requirement 19.9 forbids a partial replay: a client that applied one would render a "
            "position and an equity curve computed from events it never received"
        )
        assert body["error"] is not None
        assert body["error"]["payload"]["code"] == pc.ERROR_HISTORY_INCOMPLETE
        assert body["error"]["payload"]["message"] == pc.HISTORY_INCOMPLETE_MESSAGE
        assert body["error"]["payload"]["recoverable"] is False
        assert _canonical_refusal(body["error"]) == _canonical_refusal(
            expected.error_frame
        ), (
            "the REST gap report is not the socket's; a client that cannot hold a socket is being "
            "told something different about the same unrecoverable gap (Requirement 19.9)"
        )
        assert body["error"]["type"] == events.PaperEvent.ERROR.value

    def test_the_gap_report_says_where_the_live_stream_is(self) -> None:
        """So a client can reload through this endpoint and resume the socket from there."""
        supabase = self._double(6)
        body = _body(_client(supabase).get(f"{EVENTS.path()}?since_sequence=-1"))

        assert body["current_sequence"] == 6
        assert body["error"]["sequence"] == 6

    def test_a_cursor_above_the_sessions_own_sequence_is_a_gap(self) -> None:
        """The client claims to hold events the session never emitted."""
        supabase = self._double(6)
        client = _client(supabase)

        body = _body(_client(supabase).get(f"{EVENTS.path()}?since_sequence=99"))
        expected = _socket_replay(supabase, since=99)

        assert body["history_incomplete"] is expected.history_incomplete is True
        assert body["events"] == []
        assert client is not None

    def test_a_counter_above_an_empty_log_is_a_gap_not_up_to_date(self) -> None:
        """"A session whose events went with it" - reported, never answered "you are current"."""
        supabase = _double(_session_row())
        supabase.sessions[0]["event_sequence"] = 4

        body = _body(_client(supabase).get(f"{EVENTS.path()}?since_sequence=0"))

        assert body["history_incomplete"] is True
        assert body["events"] == []
        assert body["error"]["payload"]["code"] == pc.ERROR_HISTORY_INCOMPLETE

    def test_an_up_to_date_client_is_told_so_rather_than_told_of_a_gap(self) -> None:
        supabase = self._double(4)
        body = _body(_client(supabase).get(f"{EVENTS.path()}?since_sequence=4"))

        assert body["history_incomplete"] is False
        assert body["error"] is None
        assert body["events"] == []
        assert body["current_sequence"] == 4

    def test_the_statement_sequence_is_the_sockets(self) -> None:
        """The gate, then the session's counter, then ONE capped select over ``paper_events``."""
        supabase = self._double(6)
        client = _client(supabase)

        _body(client.get(EVENTS.path()))

        assert _issued(supabase) == [
            ("select", repo.SESSIONS_TABLE),
            ("select", repo.SESSIONS_TABLE),
            ("select", repo.EVENTS_TABLE),
        ]
        assert len(_selects(supabase, repo.EVENTS_TABLE)) == 1
        read = _selects(supabase, repo.EVENTS_TABLE)[0]
        assert read.filter_value("user_id") == USER
        assert read.filter_value("session_id") == SESSION
        assert read.limit_value == repo.SESSION_EVENT_REPLAY_CAP

    def test_another_tenants_log_is_never_fetched(self) -> None:
        supabase = self._double(3)
        supabase.events.append(
            dict(supabase.events[0], id="foreign", user_id=OTHER_USER, sequence=99)
        )
        client = _client(supabase)

        body = _body(client.get(EVENTS.path()))

        assert [frame["sequence"] for frame in body["events"]] == [1, 2, 3]
        assert OTHER_USER not in body_text(body)


# ══════════════════════════════════════════════════════════════════════════
# 11. THIS FILE'S OWN DISCIPLINE
# ══════════════════════════════════════════════════════════════════════════


def test_this_module_never_calls_asyncio_run() -> None:
    """The paper suite has ONE event loop, and it is ``_run_coroutine``'s.

    A loop per call exhausted the machine's ephemeral port range and hung this suite, which is why
    every paper test borrows ``tests/test_paper_order_lifecycle_writes._run_coroutine``. Asserted
    rather than remembered, because the failure it prevents looks like a hang rather than like a
    test failure.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    occurrences = [
        line
        for line in source.splitlines()
        if "asyncio.run(" in line and "never" not in line
    ]
    assert occurrences == [], occurrences


def test_the_audit_entry_never_changes_a_sub_resource_refusal(monkeypatch: Any) -> None:
    """A failed Audit_Log write is logged where the record would have been, and the 404 stands.

    Requirement 21.4 asks for both an indistinguishable refusal AND an audit entry; the entry is
    written by ``_owned_session`` through the one helper task 28.1 added, and a failure to write it
    must not turn the 404 into a 500. Driven through ``_run_coroutine`` - the process's one loop.
    """
    import backend_app.core.audit_trail as audit_trail

    def _boom() -> Any:
        raise RuntimeError("the audit facility is unavailable")

    monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", _boom)
    assert (
        _run_coroutine(
            paper_trading._audit_session_reference_refused(
                actor_id=USER, session_id=SESSION
            )
        )
        is None
    )

    response = _client(_double(_session_row(user_id=OTHER_USER))).get(
        f"/api/paper/sessions/{SESSION}/orders"
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
