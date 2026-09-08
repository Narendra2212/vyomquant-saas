"""
tests/test_task_28_1_session_routes.py - the seven Paper_Session lifecycle routes.

Spec: marketplace-subscriptions-paper-trading task 28.1. Requirements 17.3, 17.7, 17.8, 17.14,
17.15, 21.1, 21.4, 21.5, 22.2, 22.3.

WHAT THIS MODULE ASSERTS, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------------
Task 28.1 adds a WIRING layer. Every validation, every state transition, every refusal and every
write belongs to ``backend/paper/paper_session_service.py`` and is proved there, by
``tests/test_task_27_session_service.py`` (the pipeline's refusal ordering, the six edges, the
stop's commit-then-release-then-report sequence, the reset's restore-and-delete-nothing). None of
that is re-proved here, and a test in this file that re-asserted a service-level rule would be a
second account of it.

What is asserted here is exactly what the route layer contributes, and each claim is one a reader
can check against the requirement it names:

1. **The route table.** Seven routes, their methods, their paths and their rate limits - and the
   six existing endpoints still resolving to their own handlers, because task 28.1 "adds routes; it
   removes, renames and retypes nothing" (Requirement 17.12).
2. **The authenticated identity, on every one of them.** ``get_current_user`` is a dependency of
   all seven, asserted against the route's resolved dependency graph rather than against the
   source, so a decorator that was removed but left in a comment cannot pass. And no identifier
   from a body, query or path participates in authorisation (Requirement 21.1): the start body
   REFUSES ``user_id``, ``owner_id``, ``tenant_id`` and ``version_id``, and the caller id the
   repository is scoped by is the one the dependency produced.
3. **The 422 echoes no supplied value** (Requirement 7.6 / 22.2). One case per field a caller might
   smuggle a strategy through, each asserting the field NAME is present and the VALUE appears
   nowhere in the response bytes.
4. **Caller-scoped in the query, not after retrieval** (Requirement 21.5). Asserted twice over:
   the identity the repository was CALLED with, and the ``user_id`` predicate on the statement the
   double recorded.
5. **Another tenant's session answers byte-identically to an unknown one** (Requirements 21.4,
   22.9). Compared as response BYTES, for the same session id, with only the per-request
   correlation id removed - so "identical" is a fact about the payload rather than a list of
   fields somebody remembered to compare.
6. **A stop is reported complete only from ``StopOutcome.complete``** (Requirement 17.8). One case
   per release that did not happen, each asserting ``complete`` is ``False`` and that the missing
   piece names itself in ``outstanding``.
7. **Money crosses the boundary as integer Minor_Units** (Requirement 18.1). The start's
   ``initial_capital_minor`` is refused as a float and returned as an ``int``; the reset reports
   the integer and does not serialise the major-unit ``Decimal``.

THE DOUBLES
-----------
``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this repository
has - bound through ``PaperTradingService.bind_persistence``, which is the handle
``paper_trading._persistence`` resolves. ``get_current_user`` is overridden the way
``tests/test_paper_api_shape_compatibility.py`` overrides it, so the identity under test is the
one the dependency produced and not a header this file invented.

``paper_session_service``'s five entry points are patched where a test is about what the ROUTE did
with their result - the venue it passed, the handle it remembered, the ``complete`` it reported -
because driving the real pipeline would load an exchange's markets over the network and would
re-prove task 27's ordering here. Where the real function is cheap and the claim is about it being
REACHED - the gate's 409, the ownership-scoped 404 - the real one runs.

Coroutines are driven with ``tests/test_paper_order_lifecycle_writes._run_coroutine``, the
process's ONE event loop. ``asyncio.run`` appears nowhere in this file: a loop per call exhausted
the machine's ephemeral port range and hung the paper suite, which is why that module owns the loop
and every paper test borrows it.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as service
from backend_app.backend.paper_trading_service import (
    PAPER_EXECUTION_ENVIRONMENT,
    PaperTradingService,
)
from backend_app.core.rate_limit import limiter
from backend_app.routers import paper_trading
from tests.test_paper_order_lifecycle_writes import _run_coroutine
from tests.test_paper_repository import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
SESSION = "33333333-3333-3333-3333-333333333333"
UNKNOWN_SESSION = "ffffffff-0000-0000-0000-000000000000"
LISTING = "55555555-5555-5555-5555-555555555555"
STRATEGY = "66666666-6666-6666-6666-666666666666"
VERSION = "77777777-7777-7777-7777-777777777777"

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1m"

#: One hundred thousand US dollars, in Minor_Units. An exact integer, because that is what the
#: column stores and what the boundary is required to carry (Requirement 18.1).
CAPITAL_MINOR = 10_000_000

NOW = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

#: The seven routes task 28.1 adds: handler name -> (method, path, rate limit).
SESSION_ROUTES: Dict[str, Tuple[str, str, str]] = {
    "start_paper_session": ("POST", "/api/paper/sessions", "10/minute"),
    "list_paper_sessions": ("GET", "/api/paper/sessions", "120/minute"),
    "get_paper_session": ("GET", "/api/paper/sessions/{session_id}", "120/minute"),
    "pause_paper_session": (
        "POST",
        "/api/paper/sessions/{session_id}/pause",
        "60/minute",
    ),
    "resume_paper_session": (
        "POST",
        "/api/paper/sessions/{session_id}/resume",
        "60/minute",
    ),
    "stop_paper_session": ("POST", "/api/paper/sessions/{session_id}/stop", "60/minute"),
    "reset_paper_session": (
        "POST",
        "/api/paper/sessions/{session_id}/reset",
        "60/minute",
    ),
}

#: The seven sub-resource reads task 28.2 adds: handler name -> (method, path, rate limit).
#:
#: Held HERE rather than only in ``tests/test_task_28_2_session_subresources.py`` because
#: :meth:`TestTheRouteTable.test_no_route_was_added_beyond_the_seven` asserts the WHOLE
#: ``/api/paper`` surface, so it has to know about them - and because that file imports this one's
#: harnesses, which makes this the direction that does not produce an import cycle. One table, read
#: by both files, so the surface assertion and the sub-resource tests cannot describe different
#: route sets.
SUBRESOURCE_ROUTES: Dict[str, Tuple[str, str, str]] = {
    "get_paper_session_orders": (
        "GET",
        "/api/paper/sessions/{session_id}/orders",
        "120/minute",
    ),
    "get_paper_session_fills": (
        "GET",
        "/api/paper/sessions/{session_id}/fills",
        "120/minute",
    ),
    "get_paper_session_positions": (
        "GET",
        "/api/paper/sessions/{session_id}/positions",
        "120/minute",
    ),
    "get_paper_session_trades": (
        "GET",
        "/api/paper/sessions/{session_id}/trades",
        "120/minute",
    ),
    "get_paper_session_equity": (
        "GET",
        "/api/paper/sessions/{session_id}/equity",
        "120/minute",
    ),
    "get_paper_session_metrics": (
        "GET",
        "/api/paper/sessions/{session_id}/metrics",
        "120/minute",
    ),
    "get_paper_session_events": (
        "GET",
        "/api/paper/sessions/{session_id}/events",
        "120/minute",
    ),
}

#: The endpoints that existed BEFORE task 28.1, with the rate limits task 28.3 requires them to
#: keep: 120/60s reads, 60/60s writes, 30/60s reset. Held here so an additive change that quietly
#: retyped one of them fails in this file as well as in the frozen shape baseline.
EXISTING_ROUTES: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("GET", "/api/paper/account"): ("get_paper_account", "120/minute"),
    ("POST", "/api/paper/account/reset"): ("reset_paper_account", "30/minute"),
    ("GET", "/api/paper/positions"): ("get_paper_positions", "120/minute"),
    ("GET", "/api/paper/orders"): ("get_paper_orders", "120/minute"),
    ("POST", "/api/paper/orders"): ("place_paper_order", "60/minute"),
    ("DELETE", "/api/paper/orders/{order_id}"): ("cancel_paper_order", "60/minute"),
    ("GET", "/api/paper/trades"): ("get_paper_trades", "120/minute"),
    ("GET", "/api/paper/summary"): ("get_paper_summary", "120/minute"),
}

#: The four operation routes, by the operation constant the service names them with.
OPERATIONS = ("pause", "resume", "stop", "reset")

#: The three columns a session response must never carry, plus the caller's own ``user_id``.
#: ``config`` is the frozen configuration (Protected_Logic-adjacent, Requirement 19.7); the other
#: two are internal identifiers the entitlement decision produced server-side (Requirements 21.1,
#: 22.9); ``user_id`` is the caller telling themselves who they are.
WITHHELD_SESSION_COLUMNS = ("config", "version_id", "source_strategy_id", "user_id")


# ══════════════════════════════════════════════════════════════════════════
# THE ROUTER'S SOURCE, FOR THE CLAIMS THAT ARE ABOUT DECORATORS
# ══════════════════════════════════════════════════════════════════════════
#
# ``slowapi`` rewrites the handler it decorates, so the rate limit is not readable off the
# function object. It is read off the source instead - the same way
# ``tests/test_task_2_4_call_site_migration`` reads it - and only for the rate limit. The auth
# dependency is asserted against the resolved dependency graph below, because that is a fact about
# what FastAPI will actually call.

_ROUTER_PATH = Path(paper_trading.__file__)
_ROUTER_SOURCE = _ROUTER_PATH.read_text(encoding="utf-8")
_ROUTER_TREE = ast.parse(_ROUTER_SOURCE)


def _decorator_sources(function_name: str) -> List[str]:
    """The decorator EXPRESSIONS on ``function_name``, as they appear in the router's source.

    ``ast.get_source_segment`` returns the expression without the leading ``@``, so the strings
    read ``limiter.limit("60/minute")``. Compared in that form rather than re-adding the ``@``,
    because the sigil is punctuation and the call is the claim.
    """
    for node in ast.walk(_ROUTER_TREE):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            return [
                ast.get_source_segment(_ROUTER_SOURCE, decorator) or ""
                for decorator in node.decorator_list
            ]
    raise AssertionError(f"{function_name} is not defined in {_ROUTER_PATH.name}")


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES AND HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _session_row(
    *,
    session_id: str = SESSION,
    user_id: str = USER,
    session_state: str = "RUNNING",
) -> Dict[str, Any]:
    """One ``paper_sessions`` row as the table holds it - including the three withheld columns.

    ``config``, ``version_id`` and ``source_strategy_id`` are present ON PURPOSE. The double
    returns whole rows (PostgREST projects; an in-process dict cannot), so the only thing that can
    keep those three out of a response is the route's own projection - and a fixture that omitted
    them would make every omission assertion in this file pass without a projection.
    """
    return {
        "id": session_id,
        "user_id": user_id,
        "session_state": session_state,
        "environment": "PAPER",
        "listing_id": LISTING,
        "version_id": VERSION,
        "source_strategy_id": STRATEGY,
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "currency": "USD",
        "initial_capital_minor": CAPITAL_MINOR,
        "market_data_source": "MARKET_DATA_SERVICE",
        "feed_state": "HEALTHY",
        "feed_transport": "redis_pubsub",
        "event_sequence": 7,
        "config": {"buy_logic": "RSI(14) < 30", "sell_logic": "RSI(14) > 70"},
        "started_at": NOW.isoformat(),
        "paused_at": None,
        "stopped_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def _double(*sessions: Dict[str, Any]) -> FakeSupabase:
    """A Persistence_Layer double holding ``sessions`` and nothing else."""
    repo.reset_persistence_probe()
    return FakeSupabase(sessions=[dict(row) for row in sessions])


def _reset_rate_limit_counters() -> None:
    """Forget what the process's rate limiter has counted so far.

    The limiter is LIVE in the test environment - in-memory storage, but armed - and every request
    in this file arrives from the same ``testclient`` host. ``POST /api/paper/sessions`` is capped
    at 10/60s, which is fewer requests than this file makes of it, so without this the eleventh
    test to post a session start would be answered 429 by a counter an earlier test filled.

    The limiter is reset rather than disabled: the decorators stay in force, so a handler that
    lost its limit still fails :class:`TestTheRouteTable`, and the 429 stays reachable for anyone
    who wants to assert it.
    """
    storage = getattr(limiter, "_storage", None)
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()


@pytest.fixture(autouse=True)
def _isolated_router() -> Any:
    """Forget the migration verdict, the bound singleton, the auth override and the runtimes.

    ``_SESSION_RUNTIMES`` is process-local by design (see its docstring), which makes it exactly
    the kind of state one test can leave behind for the next. Cleared on both sides, as are the
    rate limiter's counters.
    """
    repo.reset_persistence_probe()
    _reset_rate_limit_counters()
    paper_trading._SESSION_RUNTIMES.clear()
    yield
    paper_trading._SESSION_RUNTIMES.clear()
    _release()
    _reset_rate_limit_counters()
    repo.reset_persistence_probe()


def _client(supabase: Any, user_id: str = USER) -> Any:
    """A ``TestClient`` authenticated as ``user_id`` with ``supabase`` as the bound handle.

    ``paper_trading._persistence`` resolves through
    ``PaperTradingService.persistence_client``, so binding the double to the singleton is what
    puts it behind all seven routes - no route-level patching, and therefore no chance of a route
    that resolved its handle some fourth way passing here.
    """
    from fastapi.testclient import TestClient

    from backend_app.backend import paper_trading_service as module
    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    instance = PaperTradingService(
        default_capital=100_000.0, default_fee_rate=0.001, default_slippage=0.0005
    )
    instance.bind_persistence(supabase)
    module._paper_service_instance = instance

    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "sub": user_id,
        "email": "paper-sessions@vyomquant.io",
        "tenant_id": user_id,
        "access_token": "",
        "role": "authenticated",
        "app_metadata": {},
    }
    return TestClient(app, raise_server_exceptions=False)


def _release() -> None:
    """Drop the dependency override and the bound singleton."""
    from backend_app.backend import paper_trading_service as module
    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    app.dependency_overrides.pop(get_current_user, None)
    module._paper_service_instance = None


class _Feed:
    """A stand-in for the ``FeedHandle`` the start records and the stop releases.

    It carries the one attribute the route reads (``market_data_source``, for the log line) and
    nothing else: the route does not call the handle, it hands it back to ``stop_session``, and a
    double with more surface would suggest otherwise.
    """

    def __init__(self) -> None:
        self.market_data_source = "MARKET_DATA_SERVICE"


class _FrozenConfig:
    """Stands in for the frozen ``SessionConfig`` ``start_session`` builds and the row records.

    The route reads NOTHING off it - it hands it to the spawner, which hands it to the loop - so
    what the assertions are about is identity: the object the loop steps with must be the object
    the pipeline created, because a re-derived configuration is a second implementation of
    Requirement 16.12 and is free to disagree with the session's row. A stand-in makes that
    identity the only thing under test, which is exactly what this layer is responsible for.
    """


#: The one config instance the fake pipeline creates, and the one the loop must receive.
PIPELINE_CONFIG = _FrozenConfig()

#: The ``paper_accounts.id`` the fake pipeline's create block produced. The loop reads positions
#: by it, so it is the session's isolated account and not the caller's default one.
ACCOUNT = "88888888-8888-8888-8888-888888888888"


class _Started:
    """What the route reads off ``start_session``'s return value, and no more."""

    def __init__(
        self, session: Dict[str, Any], feed: _Feed, loop_task: Any = None
    ) -> None:
        self.session = session
        self.feed = feed
        self.loop_task = loop_task

    @property
    def session_id(self) -> str:
        return str(self.session.get("id"))


def _start_body(**overrides: Any) -> Dict[str, Any]:
    """A well-formed start body: one strategy reference, the capital, currency, market, timeframe."""
    body: Dict[str, Any] = {
        "listing_id": LISTING,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "initial_capital_minor": CAPITAL_MINOR,
        "currency": "USD",
    }
    body.update(overrides)
    return body


async def _idle_loop() -> int:
    """A session loop that waits to be cancelled. Never ``asyncio.run``, never a busy spin.

    It stands in for ``session_loop``'s BODY and nothing else. What one bar does - the tick, the
    evaluation, the signal, the order, the equity point - is
    ``tests/test_task_27_session_service.py``'s subject; stepping the real body here would drive it
    against a ``FeedHandle`` stand-in that answers no event, and a loop that re-entered on every
    failed step would spin the request's event loop rather than test this layer.
    """
    await asyncio.Event().wait()
    return 0


def _patch_start(monkeypatch: Any, feed: Optional[_Feed] = None) -> List[Dict[str, Any]]:
    """Replace ``start_session`` with a recorder that STILL DRIVES the loop seam.

    The recorder stands in for the PIPELINE, not for the seam. It calls whatever ``spawn_loop`` the
    route handed it, with the two values the real pipeline supplies - the frozen configuration it
    froze and the account its create block made - and carries what the spawner produced back on
    ``StartedSession.loop_task``, which is what the real ``_spawn`` does. A fake that skipped that
    call would let every wiring assertion in this file pass with no loop installed anywhere, which
    is the state task 29.3 found: a session ``RUNNING``, reporting ``HEALTHY``, serving truthful
    zeros forever.

    So the task each start produces here is a REAL ``asyncio.Task``, created by the production
    spawner on the request's own running loop. Only the loop's body is stubbed - see
    :func:`_idle_loop` - and each call's record carries what the loop was constructed with under
    ``calls[i]["loop"]``.
    """
    calls: List[Dict[str, Any]] = []
    handle = feed if feed is not None else _Feed()

    def _fake_session_loop(
        supabase: Any, session: Any, handle_: Any, **kwargs: Any
    ) -> Any:
        # NOT an ``async def``: the arguments are recorded when the spawner CONSTRUCTS the
        # coroutine, which happens inside the request, rather than whenever the task first runs.
        if calls:
            calls[-1]["loop"] = {
                "supabase": supabase,
                "session": session,
                "feed": handle_,
                **kwargs,
            }
        return _idle_loop()

    async def _fake(supabase: Any, caller: Any, **kwargs: Any) -> _Started:
        calls.append({"supabase": supabase, "caller": caller, **kwargs})
        session = _session_row()
        spawn = kwargs.get("spawn_loop")
        loop_task = None
        if spawn is not None:
            loop_task = spawn(
                session, handle, config=PIPELINE_CONFIG, account_id=ACCOUNT
            )
        calls[-1]["loop_task"] = loop_task
        # Recorded HERE, inside the request, because the ``TestClient`` closes the event loop it
        # ran the request on as soon as the response is returned - and its runner cancels every
        # task still on that loop. So "the spawner returned a task that was actually running" is
        # only observable while the loop is alive, and a ``done()`` check after the response would
        # be a fact about the test transport rather than about the wiring.
        calls[-1]["loop_task_running_at_spawn"] = (
            isinstance(loop_task, asyncio.Task) and not loop_task.done()
        )
        return _Started(session, handle, loop_task=loop_task)

    monkeypatch.setattr(service, "session_loop", _fake_session_loop)
    monkeypatch.setattr(service, "start_session", _fake)
    return calls


def _finals(committed: bool = True) -> service.SessionFinals:
    """A ``SessionFinals`` that either committed both rows or committed neither."""
    if committed:
        return service.SessionFinals(
            snapshot={"id": "snap-1"}, metrics_row={"id": "metrics-1"}, series_index=0
        )
    return service.SessionFinals(reason="NO_ACCOUNT")


def _stop_outcome(
    *,
    finals_committed: bool = True,
    mds_released: bool = True,
    subscription_released: bool = True,
    registrations_closed: Optional[int] = 0,
) -> service.StopOutcome:
    return service.StopOutcome(
        operation=service.OPERATION_STOP,
        from_state="RUNNING",
        to_state="STOPPED",
        session=_session_row(session_state="STOPPED"),
        event_sequence=8,
        at=NOW,
        finals=_finals(finals_committed),
        loop_settled=False,
        mds_released=mds_released,
        subscription_released=subscription_released,
        registrations_closed=registrations_closed,
    )


def _reset_outcome() -> service.ResetOutcome:
    return service.ResetOutcome(
        operation=service.OPERATION_RESET,
        from_state="STOPPED",
        to_state="CREATED",
        session=_session_row(session_state="CREATED"),
        event_sequence=9,
        at=NOW,
        initial_capital_minor=CAPITAL_MINOR,
        initial_capital=Decimal("100000"),
        cancelled_orders=("order-1",),
        orders_not_cancellable=(),
        closed_positions=(SYMBOL,),
        previous_series_index=0,
        series_index=1,
    )


def _operation_outcome(operation: str, from_state: str, to_state: str) -> Any:
    return service.OperationOutcome(
        operation=operation,
        from_state=from_state,
        to_state=to_state,
        session=_session_row(session_state=to_state),
        event_sequence=8,
        at=NOW,
    )


def _comparable(response: Any) -> str:
    """One refusal body, with the per-request correlation id removed, as canonical JSON.

    ``request_id`` is the only member that legitimately differs between two requests, so it is the
    only one dropped. Everything else - the code, the public sentence, every key of ``details`` and
    every value in it - is compared, which is what makes "byte-identical" a claim about the payload
    rather than about the fields a reader thought to list.
    """
    body = response.json()
    body.pop("request_id", None)
    return json.dumps(body, sort_keys=True)


# ══════════════════════════════════════════════════════════════════════════
#  1. THE ROUTE TABLE (task 28.1's seven, and task 28.3's eight untouched)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRouteTable:
    """Seven added, eight unchanged, each with the rate limit task 28.1 names.

    Red run: the ``/reset`` route's decorator was changed to ``@limiter.limit("120/minute")`` and
    ``test_each_session_route_carries_its_rate_limit`` failed naming the handler; the
    ``@router.get("/sessions")`` path was changed to ``/session`` and both the registration test
    and every list test failed.
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

    def test_the_seven_session_routes_are_registered(self) -> None:
        registered = self._registered()
        expected = {
            (method, path): name for name, (method, path, _) in SESSION_ROUTES.items()
        }
        missing = {key: name for key, name in expected.items() if key not in registered}
        assert missing == {}, f"task 28.1's routes are not registered: {missing}"
        wrong = {
            key: (registered[key], name)
            for key, name in expected.items()
            if registered[key] != name
        }
        assert wrong == {}, f"a session route resolves to another handler: {wrong}"

    def test_the_existing_endpoints_still_resolve_to_their_own_handlers(self) -> None:
        """Requirement 17.12: this task adds routes; it removes, renames and retypes nothing."""
        registered = self._registered()
        for key, (name, _limit) in EXISTING_ROUTES.items():
            assert key in registered, (
                f"{key[0]} {key[1]} is no longer registered; task 28.3 requires the existing "
                f"endpoints to keep their paths and methods"
            )
            assert registered[key] == name, (
                f"{key[0]} {key[1]} now resolves to {registered[key]!r} rather than to its own "
                f"handler {name!r}"
            )

    def test_no_route_was_added_beyond_the_seven(self) -> None:
        """The paper surface is the existing eight, task 28.1's seven and task 28.2's seven.

        :data:`SUBRESOURCE_ROUTES` was added to the expected set when task 28.2 landed. That is an
        ADDITIVE update and not a loosened assertion: the set is still exact, so an eighth route
        added to either task still fails here, and every path in it is still named explicitly. What
        would be a loosening is dropping the equality or matching a prefix, and neither was done.
        """
        registered = set(self._registered())
        expected = (
            set(EXISTING_ROUTES)
            | {(method, path) for method, path, _ in SESSION_ROUTES.values()}
            | {(method, path) for method, path, _ in SUBRESOURCE_ROUTES.values()}
        )
        assert registered == expected, (
            f"the /api/paper surface is not the eight existing endpoints plus task 28.1's seven "
            f"and task 28.2's seven: unexpected {sorted(registered - expected)}, absent "
            f"{sorted(expected - registered)}"
        )

    @pytest.mark.parametrize("handler", sorted(SESSION_ROUTES))
    def test_each_session_route_carries_its_rate_limit(self, handler: str) -> None:
        _method, _path, limit = SESSION_ROUTES[handler]
        decorators = _decorator_sources(handler)
        assert f'limiter.limit("{limit}")' in decorators, (
            f"{handler} does not carry task 28.1's {limit} rate limit; its decorators are "
            f"{decorators}"
        )

    @pytest.mark.parametrize("handler", sorted({n for n, _ in EXISTING_ROUTES.values()}))
    def test_each_existing_endpoint_keeps_its_rate_limit(self, handler: str) -> None:
        limit = next(lim for name, lim in EXISTING_ROUTES.values() if name == handler)
        assert f'limiter.limit("{limit}")' in _decorator_sources(handler), (
            f"{handler}'s rate limit changed; task 28.3 requires the existing endpoints to keep "
            f"their 120/60s reads, 60/60s writes and 30/60s reset"
        )


# ══════════════════════════════════════════════════════════════════════════
#  2. THE AUTHENTICATED IDENTITY, ON EVERY ROUTE (Requirement 21.1)
# ══════════════════════════════════════════════════════════════════════════


class TestEveryRouteCarriesTheAuthenticatedIdentity:
    """``get_current_user`` on all seven, asserted against the resolved dependency graph.

    Not against the source: a ``Depends`` that was deleted while the import stayed would still
    read correctly in the file. FastAPI's ``dependant`` tree is what it will actually call.

    Red run: ``user: dict = Depends(get_current_user)`` was removed from ``list_paper_sessions``
    and replaced by a ``user_id`` query parameter. This class failed naming the handler, and
    ``TestTheListIsCallerScopedInTheQuery`` failed because the identity was then a client value.
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

    @pytest.mark.parametrize("handler", sorted(SESSION_ROUTES))
    def test_the_route_depends_on_get_current_user(self, handler: str) -> None:
        from backend_app.core.dependencies import get_current_user
        from backend_app.main import app

        route = next(
            r for r in app.routes if getattr(r, "name", "") == handler
        )
        assert get_current_user in self._dependencies(route), (
            f"{handler} does not depend on get_current_user; every task 28.1 route carries the "
            f"authenticated identity (Requirement 21.1)"
        )

    def test_no_session_route_accepts_an_identity_parameter(self) -> None:
        """No ``user_id``, ``owner_id`` or ``tenant_id`` query or path parameter, anywhere.

        The path parameter every operation route DOES carry is ``session_id``, which is a resource
        reference and not an identity: it is scoped BY the authenticated identity rather than
        standing in for it.
        """
        from backend_app.main import app

        forbidden = {"user_id", "owner_id", "tenant_id", "author_id", "version_id"}
        offenders: Dict[str, List[str]] = {}
        for handler in SESSION_ROUTES:
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
            f"a session route accepts an identity from the request: {offenders}; no identifier "
            f"from a body, query or path may participate in authorisation (Requirement 21.1)"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. THE START BODY ACCEPTS SIX FIELDS AND ECHOES NOTHING (Req 7.6, 22.2)
# ══════════════════════════════════════════════════════════════════════════

#: What a caller might try to smuggle through, and the value each attempt carries. The value is a
#: distinctive string so "it was not echoed" is checkable against the whole response body rather
#: than against the fields a reader thought to look at.
SMUGGLED = "d0n0tech0thisback"

UNACCEPTED_FIELDS = [
    ("strategy_definition", {"buy_logic": SMUGGLED}),
    ("graph", {"nodes": [SMUGGLED]}),
    ("compiled_plan", {"action_symbols": {"a": SMUGGLED}}),
    ("plan", SMUGGLED),
    ("indicators", [SMUGGLED]),
    ("ml_model_path", SMUGGLED),
    ("version_id", SMUGGLED),
    ("user_id", SMUGGLED),
    ("owner_id", SMUGGLED),
    ("tenant_id", SMUGGLED),
    ("subscription_id", SMUGGLED),
    ("exchange_id", SMUGGLED),
]


class TestTheStartBodyAcceptsNothingElse:
    """``extra="forbid"``, and a 422 that names the field and echoes no value.

    Red run: ``_parse_session_start``'s allow-list check was removed so the ``extra="forbid"``
    model produced the refusal instead. Every case in
    :meth:`test_an_unaccepted_field_is_refused_without_its_value` failed on the second assertion -
    FastAPI's serialiser put the rejected ``input`` value in the body, which for
    ``compiled_plan`` is somebody's strategy coming straight back out (Requirement 7.6).
    """

    def test_the_allow_list_is_exactly_the_models_fields(self) -> None:
        """The hand-written check and the model cannot describe different bodies.

        Two layers exist deliberately - the allow-list produces the response, ``extra="forbid"``
        is the backstop - and a field the model accepted but the allow-list did not would be a 422
        nobody could satisfy.
        """
        assert paper_trading._SESSION_START_ALLOWED_FIELDS == set(
            paper_trading.PaperSessionStartRequest.model_fields
        )
        assert paper_trading._SESSION_START_ALLOWED_FIELDS == {
            "listing_id",
            "strategy_id",
            "initial_capital_minor",
            "currency",
            "symbol",
            "timeframe",
            "idempotency_key",
        }, "task 28.1 names exactly these fields and no others"

    def test_the_model_forbids_extra_fields(self) -> None:
        assert (
            paper_trading.PaperSessionStartRequest.model_config.get("extra") == "forbid"
        )

    @pytest.mark.parametrize("field,value", UNACCEPTED_FIELDS, ids=[f for f, _ in UNACCEPTED_FIELDS])
    def test_an_unaccepted_field_is_refused_without_its_value(
        self, field: str, value: Any
    ) -> None:
        client = _client(_double())
        response = client.post("/api/paper/sessions", json=_start_body(**{field: value}))

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == paper_trading.PAPER_SESSION_REQUEST_INVALID
        assert field in detail["unexpected_fields"], detail
        assert SMUGGLED not in response.text, (
            f"the 422 echoed the value supplied for {field!r}; task 28.1 requires a refusal that "
            f"echoes no supplied value (Requirement 7.6)"
        )

    def test_a_fractional_capital_is_refused_and_not_echoed(self) -> None:
        """Money crosses this boundary as an exact whole number of Minor_Units or not at all."""
        client = _client(_double())
        response = client.post(
            "/api/paper/sessions",
            json=_start_body(initial_capital_minor=100000.5),
        )

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["invalid_fields"] == ["initial_capital_minor"], detail
        assert "100000.5" not in response.text

    @pytest.mark.parametrize("capital", ["10000000", True, None])
    def test_a_capital_that_is_not_an_integer_is_refused(self, capital: Any) -> None:
        client = _client(_double())
        response = client.post(
            "/api/paper/sessions", json=_start_body(initial_capital_minor=capital)
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["invalid_fields"] == ["initial_capital_minor"]

    def test_naming_neither_strategy_reference_is_refused(self) -> None:
        body = _start_body()
        body.pop("listing_id")
        client = _client(_double())
        response = client.post("/api/paper/sessions", json=body)

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["expected_one_of"] == ["listing_id", "strategy_id"]
        assert detail["supplied_fields"] == []

    def test_naming_both_strategy_references_is_refused(self) -> None:
        client = _client(_double())
        response = client.post(
            "/api/paper/sessions", json=_start_body(strategy_id=STRATEGY)
        )

        assert response.status_code == 422, response.text
        assert sorted(response.json()["detail"]["supplied_fields"]) == [
            "listing_id",
            "strategy_id",
        ]

    def test_a_body_that_is_not_an_object_is_refused(self) -> None:
        client = _client(_double())
        response = client.post("/api/paper/sessions", json=[SMUGGLED])

        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == (
            paper_trading.PAPER_SESSION_REQUEST_INVALID
        )
        assert SMUGGLED not in response.text


# ══════════════════════════════════════════════════════════════════════════
#  4. WHAT THE START HANDS THE PIPELINE, AND WHAT IT REPORTS BACK
# ══════════════════════════════════════════════════════════════════════════


class TestTheStartWiring:
    """The identity, the venue, the measurement seam, the handle - and the projected body.

    Red run: ``exchange_id=_paper_venue()`` was replaced with ``exchange_id=body.symbol`` and
    :meth:`test_the_venue_is_the_servers_own_configuration` failed; ``_remember_runtime`` was
    commented out and :meth:`test_the_feed_handle_is_remembered_for_the_stop` and
    ``TestTheStopReportsCompleteOnlyFromTheOutcome`` both failed.
    """

    def test_the_pipeline_receives_the_authenticated_identity(
        self, monkeypatch: Any
    ) -> None:
        """Requirement 21.1: the caller is the dependency's, and nothing from the request."""
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        assert response.status_code == 201, response.text
        assert len(calls) == 1
        caller = calls[0]["caller"]
        assert caller["id"] == USER
        assert caller["access_token"] == ""

    def test_the_venue_is_the_servers_own_configuration(self, monkeypatch: Any) -> None:
        """SB-06: the caller chooses the market, the operator chooses the feed."""
        monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        client.post("/api/paper/sessions", json=_start_body())

        assert calls[0]["exchange_id"] == "kraken"
        assert calls[0]["symbol"] == SYMBOL
        assert calls[0]["timeframe"] == TIMEFRAME
        assert calls[0]["initial_capital_minor"] == CAPITAL_MINOR
        assert calls[0]["currency"] == "USD"

    def test_no_venue_configured_is_the_catalogues_refusal(
        self, monkeypatch: Any
    ) -> None:
        """And it names no venue to the caller: a client learns only that data is unavailable."""
        monkeypatch.delenv("DEFAULT_EXCHANGE", raising=False)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PAPER_MARKET_DATA_UNAVAILABLE"
        assert calls == [], "the pipeline was entered without a venue"

    def test_the_measurements_come_from_the_recorded_seam(
        self, monkeypatch: Any
    ) -> None:
        """``None`` today, and reported as ``None`` rather than as an invented measurement.

        The recorded latency experiment is BLOCKED in this deployment, so there is nothing to
        return - and passing a plausible pair instead is the substitution
        ``paper_market_feed`` refuses by name.
        """
        assert paper_trading._recorded_measurements() is None
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        client.post("/api/paper/sessions", json=_start_body())

        assert calls[0]["measurements"] is None

    def test_the_response_withholds_the_three_columns_and_the_callers_own_id(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        _patch_start(monkeypatch)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        session = response.json()["session"]
        for column in WITHHELD_SESSION_COLUMNS:
            assert column not in session, (
                f"the session body carries {column!r}; the frozen configuration and the two "
                f"server-side identifiers must not reach a caller (Requirements 19.7, 21.1, 22.9)"
            )
        assert "RSI(14)" not in response.text

    def test_the_response_carries_the_feed_record_and_the_environment(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        _patch_start(monkeypatch)
        client = _client(_double())

        body = client.post("/api/paper/sessions", json=_start_body()).json()

        assert body["execution_environment"] == PAPER_EXECUTION_ENVIRONMENT
        assert body["is_simulated"] is True
        assert body["session"]["feed_state"] == "HEALTHY"
        assert body["session"]["market_data_source"] == "MARKET_DATA_SERVICE"
        assert body["session"]["event_sequence"] == 7

    def test_the_capital_is_reported_as_integer_minor_units(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        _patch_start(monkeypatch)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        capital = response.json()["session"]["initial_capital_minor"]
        assert isinstance(capital, int) and not isinstance(capital, bool)
        assert capital == CAPITAL_MINOR

    def test_the_idempotency_key_is_echoed_and_the_header_is_accepted(
        self, monkeypatch: Any
    ) -> None:
        """Accepted, shape-checked and echoed - and honestly not a duplicate guard."""
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        _patch_start(monkeypatch)
        client = _client(_double())

        from_header = client.post(
            "/api/paper/sessions",
            json=_start_body(),
            headers={"Idempotency-Key": "header-key"},
        )
        from_body = client.post(
            "/api/paper/sessions", json=_start_body(idempotency_key="body-key")
        )

        assert from_header.json()["idempotency_key"] == "header-key"
        assert from_body.json()["idempotency_key"] == "body-key"

    def test_the_feed_handle_and_the_loop_task_are_remembered_for_the_stop(
        self, monkeypatch: Any
    ) -> None:
        """Both objects a stop has to release, and the loop task is now a real one.

        This assertion USED to read ``held.loop_task is None``, and that was a faithful record of
        the state task 29.3 found: no loop was spawned in production, so the slot the stop settles
        was always empty. It is corrected rather than loosened - the task must exist, must be an
        ``asyncio.Task``, and must be the one the seam produced - because ``stop_session`` settles
        it before it commits a closing figure (Requirement 17.8, step 3) and a ``None`` there is a
        stop with nothing to settle.
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        handle = _Feed()
        _patch_start(monkeypatch, feed=handle)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        session_id = response.json()["session_id"]
        held = paper_trading._SESSION_RUNTIMES[session_id]
        assert held.feed is handle
        assert held.user_id == USER
        assert isinstance(held.loop_task, asyncio.Task)
        assert held.loop_task.get_name() == f"paper-session-loop:{session_id}"

    def test_the_pipeline_is_handed_a_spawner_rather_than_left_at_the_default(
        self, monkeypatch: Any
    ) -> None:
        """The whole of task 29.3: ``spawn_loop`` is supplied, so a started session runs.

        Left unset, ``start_session`` falls back to ``no_session_loop`` - which logs at ERROR that
        the session is ``RUNNING`` with nothing draining its feed, and then the session produces no
        bar, no signal, no order and no equity point while reporting ``RUNNING`` and ``HEALTHY``
        (Requirements 17.10, 28.5).
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        client.post("/api/paper/sessions", json=_start_body())

        assert calls[0]["spawn_loop"] is not None, (
            "the route left spawn_loop at no_session_loop; the session would be RUNNING with "
            "nothing draining its feed"
        )
        assert callable(calls[0]["spawn_loop"])
        assert calls[0]["loop_task_running_at_spawn"] is True

    def test_the_loop_steps_with_the_pipelines_own_config_and_account(
        self, monkeypatch: Any
    ) -> None:
        """The two values the seam widened for, passed through this layer untouched.

        Asserted by identity. Re-deriving the frozen configuration here would be a second
        implementation of Requirement 16.12 - one free to disagree with the ``config`` column the
        session stores - and an equal-but-not-identical object is what that would look like.
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        supabase = _double()
        client = _client(supabase)

        client.post("/api/paper/sessions", json=_start_body())

        loop = calls[0]["loop"]
        assert loop["config"] is PIPELINE_CONFIG
        assert loop["account_id"] == ACCOUNT
        assert loop["session"]["id"] == SESSION
        # The caller's RLS-scoped handle, so the loop's own statements run under the identity that
        # started the session rather than under some fourth resolution of a client.
        assert loop["supabase"] is supabase

    def test_the_loop_is_driven_by_the_platforms_runtime_and_its_signal_recorder(
        self, monkeypatch: Any
    ) -> None:
        """``evaluate``, ``plan`` and ``record_signal`` come from ``paper_session_runtime``.

        The two builders are replaced with recorders so this case is about the WIRING and does not
        import the DAG engine: what it pins is that the route resolves the strategy version the
        session row records - the one the entitlement decision named, server-side - and hands the
        session's own id, symbol and timeframe to the evaluator, then passes all three products to
        the loop. What the builders themselves produce is
        ``tests/test_task_29_signal_environments.py``'s subject.
        """
        from backend_app.backend import paper_session_runtime as runtime

        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        supabase = _double()
        supabase.strategy_versions.append(
            {"id": VERSION, "lifecycle_state": "PUBLISHED", "compiled_plan": {}}
        )
        built: List[Dict[str, Any]] = []

        def _evaluator(
            *,
            session_id: Any,
            symbol: Any,
            timeframe: Any,
            version_row: Any,
            registry: Any = None,
        ) -> Any:
            built.append(
                {
                    "builder": "evaluator",
                    "session_id": session_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "version_row": version_row,
                }
            )
            return "the-evaluator", "the-plan"

        def _recorder(sb: Any, *, session: Any, version_row: Any = None) -> Any:
            built.append(
                {"builder": "recorder", "supabase": sb, "version_row": version_row}
            )
            return "the-recorder"

        monkeypatch.setattr(runtime, "build_session_evaluator", _evaluator)
        monkeypatch.setattr(runtime, "build_session_signal_recorder", _recorder)
        client = _client(supabase)

        client.post("/api/paper/sessions", json=_start_body())

        loop = calls[0]["loop"]
        assert loop["evaluate"] == "the-evaluator"
        assert loop["plan"] == "the-plan"
        assert loop["record_signal"] == "the-recorder", (
            "the loop was spawned with no Signal_Trace recorder, so every signal it produced "
            "would be logged as unrecorded (Requirement 23.5)"
        )
        assert [entry["builder"] for entry in built] == ["evaluator", "recorder"]
        assert built[0]["session_id"] == SESSION
        assert built[0]["symbol"] == SYMBOL
        assert built[0]["timeframe"] == TIMEFRAME
        assert built[0]["version_row"]["id"] == VERSION
        assert built[1]["version_row"]["id"] == VERSION, (
            "the recorder describes a different strategy version from the evaluator"
        )

    def test_a_version_whose_plan_is_unresolvable_still_gets_a_loop_and_says_so(
        self, monkeypatch: Any, caplog: Any
    ) -> None:
        """``(None, None)`` is a reported condition, not a refusal and not a silent success.

        The session exists and is ``RUNNING`` by the time the loop is spawned, so the feed still
        has to be drained and the equity still has to be revalued. The absence of an evaluator is
        logged at ERROR by the runtime and again on every bar by ``step_session``, which is what
        lets an operator tell "produced no signal" from "evaluated nothing".
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        with caplog.at_level("ERROR", logger="PaperSessionRuntime"):
            client.post("/api/paper/sessions", json=_start_body())

        loop = calls[0]["loop"]
        assert loop["evaluate"] is None and loop["plan"] is None
        assert loop["record_signal"] is not None
        assert calls[0]["loop_task_running_at_spawn"] is True
        assert any(
            "no compiled plan to evaluate" in record.getMessage()
            for record in caplog.records
        )

    def test_the_installed_spawner_returns_a_running_task_and_leaks_nothing(
        self, monkeypatch: Any
    ) -> None:
        """The spawner's product, on a loop this case owns: a live task that cancels cleanly.

        Driven through ``_run_coroutine`` on the harness's ONE event loop rather than through the
        ``TestClient`` - see that helper's ``_HARNESS_LOOP`` note - because the two facts worth
        asserting about a spawned task are only observable while its loop is alive: that it is
        RUNNING when the spawner hands it over, and that cancelling it leaves nothing pending. The
        loop's body is stubbed for the reason :func:`_idle_loop` gives.
        """
        supabase = _double()
        records: List[Dict[str, Any]] = []

        def _fake_session_loop(sb: Any, session: Any, handle: Any, **kwargs: Any) -> Any:
            records.append(kwargs)
            return _idle_loop()

        monkeypatch.setattr(service, "session_loop", _fake_session_loop)
        spawn = paper_trading._session_loop_spawner(supabase)

        async def drive() -> Tuple[Any, List[Any]]:
            task = spawn(
                _session_row(), _Feed(), config=PIPELINE_CONFIG, account_id=ACCOUNT
            )
            assert isinstance(task, asyncio.Task)
            assert task.done() is False, (
                "the spawner returned a task that was already finished; nothing would drain the "
                "session's feed"
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            left_behind = [
                pending
                for pending in asyncio.all_tasks()
                if pending is not asyncio.current_task() and not pending.done()
            ]
            return task, left_behind

        task, left_behind = _run_coroutine(drive())

        assert task.cancelled() is True
        assert left_behind == [], f"the cancelled loop left {left_behind} behind"
        assert records[0]["config"] is PIPELINE_CONFIG
        assert records[0]["account_id"] == ACCOUNT

    def test_a_strategy_reference_is_translated_to_the_listing_it_is_keyed_on(
        self, monkeypatch: Any
    ) -> None:
        """A key translation, not a second admission decision."""
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        supabase = _double()
        supabase.library_strategies.append(
            {"id": LISTING, "source_strategy_id": STRATEGY}
        )
        client = _client(supabase)

        body = _start_body(strategy_id=STRATEGY)
        body.pop("listing_id")
        response = client.post("/api/paper/sessions", json=body)

        assert response.status_code == 201, response.text
        assert calls[0]["listing_id"] == LISTING

    def test_a_strategy_no_listing_backs_is_passed_through_unchanged(
        self, monkeypatch: Any
    ) -> None:
        """So ``resolve`` answers ``LISTING_UNAVAILABLE`` - one refusal, not two."""
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        client = _client(_double())

        body = _start_body(strategy_id=STRATEGY)
        body.pop("listing_id")
        client.post("/api/paper/sessions", json=body)

        assert calls[0]["listing_id"] == STRATEGY

    def test_a_malformed_strategy_reference_is_a_422(self) -> None:
        client = _client(_double())
        response = client.post(
            "/api/paper/sessions", json=_start_body(listing_id="not-a-uuid")
        )
        assert response.status_code == 422, response.text


# ══════════════════════════════════════════════════════════════════════════
#  5. THE LIST IS CALLER-SCOPED IN THE QUERY (Requirement 21.5)
# ══════════════════════════════════════════════════════════════════════════


class TestTheListIsCallerScopedInTheQuery:
    """Scoped in the statement, never filtered after retrieval.

    Asserted from both ends: the identity the repository was CALLED with, and the ``user_id``
    predicate the double recorded on the statement it issued. The second is what makes the claim
    about the statement rather than about the rows.

    Red run: ``list_sessions(supabase, caller_id, …)`` was changed to read every session and
    filter the result in the handler. :meth:`test_the_statement_carries_the_caller_as_a_predicate`
    failed - the statement had no ``user_id`` predicate at all, which is exactly the
    fetch-then-drop Requirement 21.5 forbids.
    """

    def test_the_repository_is_called_with_the_authenticated_identity(
        self, monkeypatch: Any
    ) -> None:
        seen: List[Tuple[Any, ...]] = []
        real = repo.list_sessions

        def _spy(supabase: Any, user_id: Any, **kwargs: Any) -> Any:
            seen.append((user_id, kwargs.get("session_state"), kwargs.get("limit")))
            return real(supabase, user_id, **kwargs)

        monkeypatch.setattr(repo, "list_sessions", _spy)
        client = _client(_double(_session_row()))

        response = client.get("/api/paper/sessions?session_state=running&limit=5")

        assert response.status_code == 200, response.text
        assert seen == [(USER, "RUNNING", 5)]

    def test_the_statement_carries_the_caller_as_a_predicate(self) -> None:
        supabase = _double(_session_row(user_id=OTHER_USER))
        client = _client(supabase)

        response = client.get("/api/paper/sessions")

        assert response.status_code == 200, response.text
        assert response.json()["sessions"] == []
        read = supabase.statements_on(repo.SESSIONS_TABLE, "select")[0]
        assert read.filter_value("user_id") == USER

    def test_another_tenants_session_is_never_returned(self) -> None:
        supabase = _double(
            _session_row(),
            _session_row(session_id=UNKNOWN_SESSION, user_id=OTHER_USER),
        )
        client = _client(supabase)

        body = client.get("/api/paper/sessions").json()

        assert [row["id"] for row in body["sessions"]] == [SESSION]
        assert body["count"] == 1

    def test_the_list_view_withholds_the_three_columns_and_the_callers_own_id(
        self,
    ) -> None:
        client = _client(_double(_session_row()))

        response = client.get("/api/paper/sessions")

        for row in response.json()["sessions"]:
            for column in WITHHELD_SESSION_COLUMNS:
                assert column not in row, f"the list element carries {column!r}"
        assert "RSI(14)" not in response.text

    def test_an_unknown_session_state_is_a_422_naming_the_permitted_values(self) -> None:
        """Rather than a statement that matches nothing and reads as "you have no sessions"."""
        client = _client(_double(_session_row()))

        response = client.get("/api/paper/sessions?session_state=HALTED")

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == paper_trading.PAPER_SESSION_REQUEST_INVALID
        assert detail["permitted"] == list(repo.SESSION_STATES)

    def test_a_read_that_did_not_complete_is_not_an_empty_list(self) -> None:
        """Requirement 28.3: "the read failed" reported as "you have none" is a fabrication."""
        supabase = _double(_session_row())
        supabase.raise_on.add(("select", repo.SESSIONS_TABLE))
        client = _client(supabase)

        response = client.get("/api/paper/sessions")

        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "PAPER_READ_FAILED"
        assert "sessions" not in body

    def test_the_limit_is_bounded_by_the_repositorys_cap(self) -> None:
        client = _client(_double(_session_row()))
        response = client.get(
            f"/api/paper/sessions?limit={repo.SESSION_LIST_CAP + 1}"
        )
        assert response.status_code == 422, response.text


# ══════════════════════════════════════════════════════════════════════════
#  6. ANOTHER TENANT'S SESSION ANSWERS AS AN UNKNOWN ONE (Req 21.4, 22.9)
# ══════════════════════════════════════════════════════════════════════════


class TestAnotherTenantsSessionAnswersAsAnUnknownOne:
    """The same session id, two doubles, one byte-identical refusal.

    The comparison is over the SAME identifier deliberately: the refusal echoes the id the caller
    supplied, so comparing two different ids would permit a difference that says nothing. Holding
    the id fixed and varying only whether the session exists for somebody else makes the response
    bytes the whole of the claim.

    Red run: ``read_session_summary``'s ``user_id`` predicate is proved in
    ``tests/test_paper_repository.py``, so the red run was taken in the route instead - the
    ``row is None`` branch was changed to answer 403 ``FORBIDDEN`` when a second unscoped read
    found the row. Both comparisons here failed: the status and the code differed, which is the
    disclosure that another tenant's session exists.
    """

    def test_the_read_route_answers_identically(self) -> None:
        foreign = _client(_double(_session_row(user_id=OTHER_USER))).get(
            f"/api/paper/sessions/{SESSION}"
        )
        _release()
        absent = _client(_double()).get(f"/api/paper/sessions/{SESSION}")

        assert foreign.status_code == absent.status_code == 404
        assert _comparable(foreign) == _comparable(absent), (
            "another tenant's session must answer exactly as an unknown one does "
            "(Requirements 21.4, 22.9)"
        )
        assert foreign.json()["error"]["details"] == {"session_id": SESSION}

    @pytest.mark.parametrize("operation", OPERATIONS)
    def test_each_operation_route_answers_identically(self, operation: str) -> None:
        foreign = _client(_double(_session_row(user_id=OTHER_USER))).post(
            f"/api/paper/sessions/{SESSION}/{operation}"
        )
        _release()
        absent = _client(_double()).post(f"/api/paper/sessions/{SESSION}/{operation}")

        assert foreign.status_code == absent.status_code == 404
        assert _comparable(foreign) == _comparable(absent)

    def test_the_refusal_discloses_no_state_and_no_owner(self) -> None:
        response = _client(_double(_session_row(user_id=OTHER_USER))).post(
            f"/api/paper/sessions/{SESSION}/pause"
        )

        assert response.status_code == 404
        assert OTHER_USER not in response.text
        for disclosure in ("RUNNING", "session_state", "owner", "author"):
            assert disclosure not in response.text, (
                f"the refusal discloses {disclosure!r}, which tells a prober the session exists"
            )

    def test_the_read_is_scoped_by_the_caller_in_the_statement(self) -> None:
        supabase = _double(_session_row(user_id=OTHER_USER))
        client = _client(supabase)

        client.get(f"/api/paper/sessions/{SESSION}")

        read = supabase.statements_on(repo.SESSIONS_TABLE, "select")[0]
        assert read.filter_value("user_id") == USER
        assert read.filter_value("id") == SESSION

    @pytest.mark.parametrize(
        "path",
        [
            "/api/paper/sessions/not-a-uuid",
            "/api/paper/sessions/not-a-uuid/pause",
            "/api/paper/sessions/not-a-uuid/resume",
            "/api/paper/sessions/not-a-uuid/stop",
            "/api/paper/sessions/not-a-uuid/reset",
        ],
    )
    def test_a_malformed_session_id_is_a_422(self, path: str) -> None:
        """``_safe_uuid`` on the path parameter, on every route that has one."""
        client = _client(_double(_session_row()))
        response = client.get(path) if path.endswith("uuid") else client.post(path)

        assert response.status_code == 422, response.text
        assert "session_id" in response.text


# ══════════════════════════════════════════════════════════════════════════
#  7. THE OPERATIONS ARE THE SERVICE'S STATE MACHINE (Req 17.7, 17.14)
# ══════════════════════════════════════════════════════════════════════════


class TestTheOperationsAreTheServicesStateMachine:
    """The route does not re-state the machine, and it does not swallow the 409.

    Red run: the ``except PaperError`` branch in ``_operate`` was changed to return
    ``{"status": "ok"}`` for ``PAPER_SESSION_OPERATION_REJECTED``, which is the "quiet success"
    the refusal exists to prevent. :meth:`test_an_illegal_operation_is_the_services_409` failed.
    """

    def test_an_illegal_operation_is_the_services_409_naming_both(self) -> None:
        """A real ``pause_session`` against a ``CREATED`` session - the gate, reached."""
        supabase = _double(_session_row(session_state="CREATED"))
        client = _client(supabase)

        response = client.post(f"/api/paper/sessions/{SESSION}/pause")

        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "PAPER_SESSION_OPERATION_REJECTED"
        assert error["details"]["session_state"] == "CREATED"
        assert error["details"]["operation"] == "pause"
        assert not supabase.wrote_anything(), "the refused operation issued a write"

    @pytest.mark.parametrize(
        "operation,from_state,to_state",
        [
            ("pause", "RUNNING", "PAUSED"),
            ("resume", "PAUSED", "RUNNING"),
        ],
    )
    def test_an_accepted_operation_reports_the_transition_and_the_sequence(
        self, monkeypatch: Any, operation: str, from_state: str, to_state: str
    ) -> None:
        outcome = _operation_outcome(operation, from_state, to_state)

        async def _fake(supabase: Any, caller: Any, session_id: Any, **kwargs: Any) -> Any:
            assert caller["id"] == USER
            assert session_id == SESSION
            return outcome

        monkeypatch.setattr(service, f"{operation}_session", _fake)
        client = _client(_double(_session_row(session_state=from_state)))

        response = client.post(f"/api/paper/sessions/{SESSION}/{operation}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == to_state.lower()
        assert body["operation"] == operation
        assert body["from_state"] == from_state
        assert body["to_state"] == to_state
        assert body["event_sequence"] == 8
        assert body["at"] == NOW.isoformat()
        assert body["execution_environment"] == PAPER_EXECUTION_ENVIRONMENT
        assert body["is_simulated"] is True
        for column in WITHHELD_SESSION_COLUMNS:
            assert column not in body["session"]

    def test_a_statement_failure_is_reported_rather_than_relabelled(
        self, monkeypatch: Any
    ) -> None:
        async def _fake(*args: Any, **kwargs: Any) -> Any:
            raise repo.PaperRepositoryError("the update did not complete")

        monkeypatch.setattr(service, "pause_session", _fake)
        client = _client(_double(_session_row()))

        response = client.post(f"/api/paper/sessions/{SESSION}/pause")

        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "PAPER_READ_FAILED"


# ══════════════════════════════════════════════════════════════════════════
#  8. THE STOP REPORTS COMPLETE ONLY FROM THE OUTCOME (Requirement 17.8)
# ══════════════════════════════════════════════════════════════════════════


class TestTheStopReportsCompleteOnlyFromTheOutcome:
    """``complete`` is ``StopOutcome.complete``, never "the call returned".

    Red run: the route was changed to report ``"complete": True`` unconditionally. Every case in
    :meth:`test_one_outstanding_release_makes_the_stop_incomplete` failed - which is the whole
    point of Requirement 17.8: a stop reported complete while a market-data subscription is still
    open is a stop that did not happen.
    """

    @staticmethod
    def _patch(monkeypatch: Any, outcome: service.StopOutcome) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []

        async def _fake(supabase: Any, caller: Any, session_id: Any, **kwargs: Any) -> Any:
            calls.append({"caller": caller, "session_id": session_id, **kwargs})
            return outcome

        monkeypatch.setattr(service, "stop_session", _fake)
        return calls

    def test_a_stop_with_every_release_done_reports_complete(
        self, monkeypatch: Any
    ) -> None:
        self._patch(monkeypatch, _stop_outcome())
        client = _client(_double(_session_row()))

        response = client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["complete"] is True
        assert body["outstanding"] == []
        assert body["to_state"] == "STOPPED"
        assert body["finals_committed"] is True

    @pytest.mark.parametrize(
        "kwargs,outstanding",
        [
            ({"finals_committed": False}, "finals"),
            ({"mds_released": False}, "mds_unsubscribe"),
            ({"subscription_released": False}, "local_subscription"),
            ({"registrations_closed": None}, "channel_registrations"),
        ],
        ids=["finals", "mds", "local_subscription", "registrations"],
    )
    def test_one_outstanding_release_makes_the_stop_incomplete(
        self, monkeypatch: Any, kwargs: Dict[str, Any], outstanding: str
    ) -> None:
        self._patch(monkeypatch, _stop_outcome(**kwargs))
        client = _client(_double(_session_row()))

        body = client.post(f"/api/paper/sessions/{SESSION}/stop").json()

        assert body["complete"] is False, (
            f"the stop was reported complete with {outstanding} outstanding; Requirement 17.8 "
            f"permits the claim only after all four have committed"
        )
        assert body["outstanding"] == [outstanding]

    def test_the_session_is_still_stopped_when_the_release_is_incomplete(
        self, monkeypatch: Any
    ) -> None:
        """A 200 with ``complete: false``, not a failure: the transition committed first."""
        self._patch(monkeypatch, _stop_outcome(subscription_released=False))
        client = _client(_double(_session_row()))

        response = client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["to_state"] == "STOPPED"
        assert body["status"] == "stopped"
        assert body["complete"] is False

    def test_an_absent_final_names_its_reason_rather_than_reporting_zero(
        self, monkeypatch: Any
    ) -> None:
        """Requirements 28.3 / 28.5: an uncomputable figure is absent, never a zero."""
        self._patch(monkeypatch, _stop_outcome(finals_committed=False))
        client = _client(_double(_session_row()))

        body = client.post(f"/api/paper/sessions/{SESSION}/stop").json()

        assert body["finals_committed"] is False
        assert body["finals_reason"] == "NO_ACCOUNT"

    def test_the_remembered_handle_is_handed_to_the_stop_and_removed(
        self, monkeypatch: Any
    ) -> None:
        handle = _Feed()
        paper_trading._remember_runtime(SESSION, USER, feed=handle, loop_task=None)
        calls = self._patch(monkeypatch, _stop_outcome())
        client = _client(_double(_session_row()))

        client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert calls[0]["feed"] is handle
        assert calls[0]["redis"] is None, (
            "a second transport was resolved beside the handle that already publishes through it"
        )
        assert SESSION not in paper_trading._SESSION_RUNTIMES

    def test_the_loop_task_the_start_spawned_is_handed_over_for_settling(
        self, monkeypatch: Any
    ) -> None:
        """Start, then stop: the task the pipeline spawned arrives as ``loop_task=``.

        That argument is Requirement 17.8's step 3 - ``stop_session`` cancels and AWAITS the loop
        before it commits a closing figure, because a bar still writing would supersede the "final"
        point. ``tests/test_task_27_session_service.py::
        test_the_loop_is_cancelled_and_awaited_before_a_final_figure_is_written`` pins that
        ordering with a real task; what is pinned HERE is that the route hands the task over at all,
        which it could not do before task 29.3 because none was ever spawned.

        The two requests run on two different ``TestClient`` event loops, so the task from the
        first is no longer running by the time the second is made. Identity is the assertion: what
        the stop is given must be the object the start remembered.
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        started = _patch_start(monkeypatch)
        calls = self._patch(monkeypatch, _stop_outcome())
        client = _client(_double(_session_row()))

        client.post("/api/paper/sessions", json=_start_body())
        remembered = paper_trading._SESSION_RUNTIMES[SESSION].loop_task

        client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert isinstance(remembered, asyncio.Task)
        assert remembered is started[0]["loop_task"]
        assert calls[0]["loop_task"] is remembered, (
            "the stop was given no loop task, so it had nothing to settle before writing the "
            "closing equity point (Requirement 17.8, step 3)"
        )
        assert SESSION not in paper_trading._SESSION_RUNTIMES

    def test_another_tenants_remembered_handle_is_not_handed_over(
        self, monkeypatch: Any
    ) -> None:
        """The scope on the process-local dict, as defence in depth behind the scoped read."""
        handle = _Feed()
        paper_trading._remember_runtime(SESSION, OTHER_USER, feed=handle, loop_task=None)
        calls = self._patch(monkeypatch, _stop_outcome(subscription_released=False))
        client = _client(_double(_session_row()))

        client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert calls[0]["feed"] is None
        assert paper_trading._SESSION_RUNTIMES[SESSION].feed is handle, (
            "another identity's runtime entry was consumed"
        )

    def test_a_stop_without_a_local_handle_still_publishes_through_a_transport(
        self, monkeypatch: Any
    ) -> None:
        """The residual gap, wired honestly: no handle here, so the ``mds`` half needs Redis."""
        calls = self._patch(monkeypatch, _stop_outcome(subscription_released=False))
        monkeypatch.setattr(paper_trading, "_mds_redis", lambda: "redis-handle")
        client = _client(_double(_session_row()))

        client.post(f"/api/paper/sessions/{SESSION}/stop")

        assert calls[0]["feed"] is None
        assert calls[0]["redis"] == "redis-handle"


# ══════════════════════════════════════════════════════════════════════════
#  9. THE RESET REPORTS MINOR UNITS AND NOTHING INEXACT (Req 17.15, 18.1)
# ══════════════════════════════════════════════════════════════════════════


class TestTheResetBody:
    """The integer, the two lists, the two series indices - and no ``Decimal`` on the wire.

    Red run: ``"initial_capital": outcome.initial_capital`` was added to the body. The response
    carried ``100000.0`` - an exact figure arriving as a binary float, which
    :meth:`test_the_major_unit_decimal_is_not_serialised` failed on.
    """

    @staticmethod
    def _patch(monkeypatch: Any) -> None:
        async def _fake(supabase: Any, caller: Any, session_id: Any, **kwargs: Any) -> Any:
            return _reset_outcome()

        monkeypatch.setattr(service, "reset_session", _fake)

    def test_the_reset_reports_the_recorded_integer_minor_units(
        self, monkeypatch: Any
    ) -> None:
        self._patch(monkeypatch)
        client = _client(_double(_session_row(session_state="STOPPED")))

        response = client.post(f"/api/paper/sessions/{SESSION}/reset")

        assert response.status_code == 200, response.text
        body = response.json()
        capital = body["initial_capital_minor"]
        assert isinstance(capital, int) and not isinstance(capital, bool)
        assert capital == CAPITAL_MINOR

    def test_the_major_unit_decimal_is_not_serialised(self, monkeypatch: Any) -> None:
        self._patch(monkeypatch)
        client = _client(_double(_session_row(session_state="STOPPED")))

        body = client.post(f"/api/paper/sessions/{SESSION}/reset").json()

        assert "initial_capital" not in body, (
            "the major-unit Decimal would reach a client as a binary float; money crosses this "
            "boundary as integer Minor_Units (Requirement 18.1)"
        )
        assert not any(isinstance(value, float) for value in body.values()), body

    def test_the_reset_reports_what_it_cancelled_closed_and_began(
        self, monkeypatch: Any
    ) -> None:
        self._patch(monkeypatch)
        client = _client(_double(_session_row(session_state="STOPPED")))

        body = client.post(f"/api/paper/sessions/{SESSION}/reset").json()

        assert body["cancelled_orders"] == ["order-1"]
        assert body["orders_not_cancellable"] == []
        assert body["closed_positions"] == [SYMBOL]
        assert body["previous_series_index"] == 0
        assert body["series_index"] == 1
        assert body["to_state"] == "CREATED"


# ══════════════════════════════════════════════════════════════════════════
# 10. THE AUDIT ENTRY NEVER CHANGES THE REFUSAL (Requirement 21.4)
# ══════════════════════════════════════════════════════════════════════════


class TestTheAuditEntryNeverChangesTheRefusal:
    """A failed Audit_Log write is logged where the record would have been, and the 404 stands.

    Driven through ``_run_coroutine`` - the process's one event loop - rather than through
    ``asyncio.run``, which the paper suite does not use anywhere.
    """

    def test_a_failing_audit_write_does_not_raise(self, monkeypatch: Any) -> None:
        import backend_app.core.audit_trail as audit_trail

        def _boom() -> Any:
            raise RuntimeError("the audit facility is unavailable")

        monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", _boom)

        # Returns None rather than raising: the refusal is returned either way.
        assert (
            _run_coroutine(
                paper_trading._audit_session_reference_refused(
                    actor_id=USER, session_id=SESSION
                )
            )
            is None
        )

    def test_the_refusal_is_still_a_404_when_the_audit_write_fails(
        self, monkeypatch: Any
    ) -> None:
        import backend_app.core.audit_trail as audit_trail

        def _boom() -> Any:
            raise RuntimeError("the audit facility is unavailable")

        monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", _boom)
        client = _client(_double(_session_row(user_id=OTHER_USER)))

        response = client.get(f"/api/paper/sessions/{SESSION}")

        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"


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
