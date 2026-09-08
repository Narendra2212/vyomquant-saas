"""
tests/test_paper_session_api.py - the retained ``/api/paper/*`` endpoints, after task 28 added
fourteen routes beside them.

Spec: marketplace-subscriptions-paper-trading task 28.3. Requirements 17.12, 22.2, 22.4.

WHAT THIS MODULE IS FOR
-----------------------
Task 28.1 added seven Paper_Session lifecycle routes and task 28.2 added seven sub-resource reads.
Task 28.3 is the claim that neither of them disturbed the eight endpoints ``api.paper`` already
consumes - ``GET /api/paper/account``, ``/positions``, ``/orders``, ``/trades``, ``/summary``,
``POST /account/reset``, ``POST /orders`` and ``DELETE /orders/{order_id}``. Requirement 17.12
states it as "extend them only additively": no path removed or renamed, no field removed, renamed
or retyped, and every addition optional for an existing consumer.

So every test here is about the boundary BETWEEN the retained eight and the added fourteen. What
each endpoint answers on its own is not re-asserted, and the four claims task 28.3 names that are
already pinned elsewhere are deferred rather than restated:

* **The frozen response shape of the retained six** is
  ``tests/test_paper_api_shape_compatibility.py`` against
  ``tests/regression/baseline/paper_api_shape.json`` - a pre-change capture that cannot be re-taken.
  Nothing here re-compares a body against it.
* **The route table** - the eight (method, path) pairs resolving to their handler NAMES, the eight
  rate limits as written in the router's source, and the ``/api/paper`` surface as a set EQUALITY -
  is ``tests/test_task_28_1_session_routes.TestTheRouteTable``. Its three tables
  (:data:`EXISTING_ROUTES`, :data:`SESSION_ROUTES`, :data:`SUBRESOURCE_ROUTES`) are IMPORTED here
  rather than restated, so this file and that one cannot describe different route sets.
* **The ``extra="forbid"`` 422 that echoes no supplied value** is
  ``test_task_28_1_session_routes.TestTheStartBodyAcceptsNothingElse``, over twelve fields a caller
  might smuggle a strategy or an identity through. This file adds the six fields that table does
  NOT contain - the retained bodies' own ``capital``, ``quantity``, ``price``, ``order_type``,
  ``side`` and ``deployment_id`` - because "the retained body cannot be posted to the session
  route" is a claim about the boundary rather than a thirteenth copy of the smuggling case.
  :func:`test_the_two_refusal_tables_do_not_overlap` keeps the two tables disjoint.
* **Caller scoping on the SESSION routes** is task 28.1's sections 5 and 6. What is asserted here
  is caller scoping on the RETAINED eight, which no module asserted at the route layer: the
  repository's ``user_id`` predicate is proved in ``tests/test_paper_repository.py``, and this is
  the claim that the retained handlers pass the dependency's identity into it.

WHAT IS ASSERTED HERE, AND NOWHERE ELSE
---------------------------------------
1. **The same handler.** Each retained (method, path) resolves to the very function object the
   router module still exposes under that name - identity, not name equality - and Starlette's own
   matcher finds EXACTLY ONE full match for each retained URL. Fourteen added paths make shadowing
   a live possibility for the first time, and a name-keyed table cannot see it.
2. **The same rate limit, in the registry that enforces it.** ``limiter._route_limits`` is what
   ``slowapi`` consults per request, and it is keyed by ``module.function`` - so two handlers
   sharing a name would MERGE their limits and a retained read could quietly inherit the session
   start's 10/60s. Task 28.1 reads the decorator out of the source; this reads what will actually
   be applied, and then exhausts one retained endpoint to show the limit is armed rather than
   merely declared (Requirement 22.4).
3. **The same body.** The two retained request models keep their fields, types and defaults, and
   the session start's ``extra="forbid"`` was NOT retrofitted onto them - tightening a retained body
   to refuse an unknown field would break exactly the consumer Requirement 17.12 protects.
4. **The same default-account semantics.** The default account is ``session_id IS NULL``. With a
   RUNNING session in existence that has its own account, orders, position, fill and ledger, every
   retained read still answers from the default account, every retained envelope still reports
   ``session_id: null``, ``?session_id=`` is not a way into a session, and the retained reset
   touches nothing of the session's. The converse too: a session sub-resource never serves a
   default-account row.

WHAT THIS FILE FOUND, AND WHAT WAS DONE ABOUT IT
------------------------------------------------
``DELETE /api/paper/orders/{order_id}`` read its target through ``paper_repository.read_order``
scoped by ``(user_id, id)`` and NOT by the default account. A caller who learned one of their own
session's order ids - from ``GET /api/paper/sessions/{id}/orders``, which reports it - could
therefore cancel a SESSION order through the retained path: the row moved to ``CANCELLED`` with its
``session_id`` intact, locked capital was released on the SESSION's account, and no
``paper_events`` row was written for any of it.

That was never a task 28 regression - ``cancel_order``'s scope had been ``(user_id, order_id)``
since task 23.2 and task 28 changed no line of it; task 28 only made it reachable, by making
session orders exist. It is now closed by NARROWING that one read to ``session_id IS NULL``
(``repo.read_order(..., session_id=None)``), which restores the default-account semantics
Requirement 17.12 documents for the retained endpoints rather than changing them: the endpoint
keeps its path, method, body, rate limit and response shape, and a session order id now answers it
exactly as an unknown id does - same status, same code, same message, carrying only the identifier
the caller supplied (Requirement 21.4).

Session orders are cancelled by the session's own RESET, which is where Requirement 17.15 puts
them and which takes the ``paper_session_service`` state machine and writes the session's
``paper_events`` record (Requirements 17.7, 19.2).
:class:`TestTheRetainedCancelIsScopedToTheDefaultAccount` is the assertion, in both directions.

THE DOUBLES AND THE LOOP
------------------------
``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this repository
has - reached through ``test_task_28_1_session_routes._double`` / ``_client`` / ``_release``, so
the wiring under test is the same wiring tasks 28.1 and 28.2 exercise. Its partial
``uq_paper_account_default`` index (``WHERE session_id IS NULL``) is what makes the default account
single and a session's account separate, which is the premise every claim in section 4 rests on.

``asyncio.run`` appears nowhere: the paper suite has ONE event loop, in
``tests/test_paper_order_lifecycle_writes._run_coroutine``, because a fresh loop per call exhausted
this machine's ephemeral port range and hung the suite. Nothing here needs to drive a coroutine
directly - every request goes through ``TestClient`` - and
:func:`test_this_module_never_calls_asyncio_run` keeps it that way.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import pytest

from backend_app.backend.paper import paper_channel as pc
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.paper_order_state import PaperOrderState
from backend_app.backend.paper_trading_service import PAPER_EXECUTION_ENVIRONMENT
from backend_app.core.rate_limit import limiter
from backend_app.routers import paper_trading
from tests.test_paper_repository import FakeSupabase
from tests.test_task_28_1_session_routes import (
    CAPITAL_MINOR,
    EXCHANGE,
    EXISTING_ROUTES,
    LISTING,
    NOW,
    OTHER_USER,
    SESSION,
    SESSION_ROUTES,
    SMUGGLED,
    SUBRESOURCE_ROUTES,
    UNACCEPTED_FIELDS,
    USER,
    _client,
    _double,
    _release,
    _reset_rate_limit_counters,
    _session_row,
    _start_body,
)
from tests.test_task_28_1_session_routes import SYMBOL as SESSION_ROW_SYMBOL

#: The fourteen routes task 28 added, as one table. Both halves are imported from task 28.1's
#: module: it owns the surface assertion, so it has to hold both, and holding a third copy here
#: would be a third description of the same route set.
ADDED_ROUTES: Dict[str, Tuple[str, str, str]] = {**SESSION_ROUTES, **SUBRESOURCE_ROUTES}

#: The QUERY and PATH parameters each retained handler declares - not its body, and not its
#: headers. Frozen here because a retained read that GAINED a ``session_id`` parameter would have
#: changed what it means without changing its path: it would no longer be the default account's
#: read, and Requirement 17.12's "no existing response field given a changed meaning" would be
#: broken by an addition that looks additive.
RETAINED_PARAMETERS: Dict[str, FrozenSet[str]] = {
    "get_paper_account": frozenset(),
    "reset_paper_account": frozenset(),
    "get_paper_positions": frozenset(),
    "get_paper_orders": frozenset({"status"}),
    "place_paper_order": frozenset(),
    "cancel_paper_order": frozenset({"order_id"}),
    "get_paper_trades": frozenset({"limit"}),
    "get_paper_summary": frozenset(),
}

#: ``PaperOrderRequest`` as it stands: field -> (annotation, required, default). Compared with the
#: real typing objects rather than with their spellings, so ``Optional[float]`` cannot pass as
#: ``float | None`` text-matched loosely.
RETAINED_ORDER_BODY: Dict[str, Tuple[Any, bool, Any]] = {
    "symbol": (str, True, None),
    "side": (str, True, None),
    "order_type": (str, False, "market"),
    "quantity": (float, True, None),
    "price": (Optional[float], False, None),
    "strategy_id": (Optional[str], False, None),
    "deployment_id": (Optional[str], False, None),
}

#: ``PaperResetRequest``, likewise. One field, and its default is the figure the retained endpoint
#: has always reset to.
RETAINED_RESET_BODY: Dict[str, Tuple[Any, bool, Any]] = {
    "capital": (float, False, 100000.0),
}

#: The retained bodies' own field names that the session start must refuse. Deliberately DISJOINT
#: from task 28.1's :data:`UNACCEPTED_FIELDS`, which covers the Protected_Logic and identity
#: fields; these are the ones a client that already talks to ``POST /api/paper/orders`` or
#: ``POST /api/paper/account/reset`` would send by mistake or on purpose. ``symbol`` and
#: ``strategy_id`` are absent from this list because the session start legitimately ACCEPTS both.
RETAINED_BODY_FIELDS_THE_SESSION_START_REFUSES = (
    "capital",
    "quantity",
    "price",
    "order_type",
    "side",
    "deployment_id",
)

#: The default account's capital, and the session account's. Different figures, so an answer that
#: came from the wrong account is visible in the number rather than only in a row id.
DEFAULT_CAPITAL = Decimal("100000")
SESSION_CAPITAL = Decimal("50000")

#: What the resting buy :func:`_seed_account_set` writes would have reserved: quantity 1 at limit
#: 100 plus the ``_client`` service's 0.001 fee rate, quantized to the currency's two places -
#: which is exactly the figure ``cancel_order`` recomputes and releases. Stated as a ``Decimal``
#: because money in this suite is never a float (Requirement 18.1).
RESERVED_ON_THE_RESTING_BUY = Decimal("100.10")

#: A well-formed order id no row carries. The comparison target for "a session order answers this
#: endpoint exactly as an unknown one does": it has to be well-formed, or the two answers would
#: differ for a reason that has nothing to do with scope.
UNKNOWN_ORDER = "dddddddd-0000-0000-0000-0000000000ff"

#: One symbol per account. Every collection assertion below is "this symbol and not that one",
#: which names the account the row came from in the failure message itself.
DEFAULT_SYMBOL = "BTC-USDT"
SESSION_SYMBOL = "ETH-USDT"
OTHER_TENANT_SYMBOL = "SOL-USDT"

#: The retained reads, as (path, the key holding the collection). ``/account`` and ``/summary`` are
#: not here: they answer with an account body rather than a collection, and they are asserted
#: separately on the figures that body reports.
RETAINED_COLLECTION_READS = (
    ("/api/paper/positions", "positions"),
    ("/api/paper/orders", "orders"),
    ("/api/paper/trades", "trades"),
)

#: The sub-resources whose rows the seeding below writes for both accounts, so the converse claim -
#: a session read does not serve a default-account row - can be made per sub-resource.
SUBRESOURCES_WITH_BOTH_SETS = ("orders", "positions", "fills")


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES AND SEEDING
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolated_router() -> Any:
    """The reset ``tests/test_task_28_1_session_routes`` performs, applied to this module too.

    Its autouse fixture is scoped to its own module, so importing its helpers does not import its
    isolation. The migration verdict, the bound singleton, the auth override, the process-local
    session runtimes, ``paper_channel``'s owner cache and the rate limiter's counters are all
    module-scope state, and module-scope state is exactly what lets one test decide another's
    answer. The limiter is RESET rather than disabled, so every decorator stays in force and
    :class:`TestTheRateLimitsAreTheOnesThatWillBeEnforced` can exhaust one on purpose.
    """
    repo.reset_persistence_probe()
    pc.invalidate_session_owner()
    _reset_rate_limit_counters()
    paper_trading._SESSION_RUNTIMES.clear()
    yield
    paper_trading._SESSION_RUNTIMES.clear()
    _release()
    pc.invalidate_session_owner()
    _reset_rate_limit_counters()
    repo.reset_persistence_probe()


def _seed_account_set(
    supabase: FakeSupabase,
    *,
    account_id: str,
    session_id: Optional[str],
    user_id: str,
    symbol: str,
    marker: str,
) -> None:
    """Write one account's whole observable set: a resting order, an open position, a closed fill.

    Written through ``paper_repository`` rather than by appending dicts, so every row carries the
    columns and the ``legacy_status`` the real writers produce - a hand-built row could satisfy a
    predicate the production statement would miss. ``session_id`` is passed through: ``None`` is
    the default account and a session id is that session's isolated set (Requirement 17.6).
    """
    resting = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=user_id,
        session_id=session_id,
        symbol=symbol,
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="100",
        reference_price="100",
        fingerprint=f"{marker}-resting",
    )
    repo.update_order(
        supabase,
        user_id=user_id,
        order_id=resting["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )

    repo.upsert_position(
        supabase,
        account_id=account_id,
        user_id=user_id,
        session_id=session_id,
        symbol=symbol,
        side="LONG",
        size="1",
        entry_price="100",
        opened_at=NOW.isoformat(),
        current_price="100",
        unrealized_pnl="0",
        price_at=NOW.isoformat(),
    )

    filled = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=user_id,
        session_id=session_id,
        symbol=symbol,
        side="buy",
        order_type="market",
        quantity="2",
        reference_price="100",
        fingerprint=f"{marker}-filled",
    )
    repo.update_order(
        supabase,
        user_id=user_id,
        order_id=filled["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )
    repo.update_order(
        supabase,
        user_id=user_id,
        order_id=filled["id"],
        order_state=PaperOrderState.FILLED,
        expected_state=PaperOrderState.ACCEPTED,
        filled_quantity="2",
        avg_fill_price="100",
        fee_minor=0,
    )
    fill = repo.insert_fill(
        supabase,
        order_id=filled["id"],
        user_id=user_id,
        session_id=session_id,
        fill_event_id=f"{marker}-fill-1",
        quantity="2",
        price="100",
        fee_minor=0,
        slippage_minor=0,
        filled_at=NOW.isoformat(),
    )
    repo.insert_balance_event(
        supabase,
        account_id=account_id,
        user_id=user_id,
        session_id=session_id,
        cause="FILL",
        available_delta="0",
        locked_delta="0",
        realized_delta="0",
        available_after="100",
        locked_after="0",
        realized_after="0",
        occurred_at=NOW.isoformat(),
        fill_id=fill["id"],
    )


def _both_accounts(
    *, second_tenant: bool = False, session_state: str = "RUNNING"
) -> FakeSupabase:
    """A double holding the caller's DEFAULT account and their RUNNING session's account.

    Both accounts are created through ``repo.get_or_create_account``, which is what makes them a
    real pair: ``uq_paper_account_default`` is PARTIAL on ``session_id IS NULL``, so the default
    account and the session account coexist only because their ``session_id`` differs - which is
    the exact distinction every claim in section 4 turns on.

    ``second_tenant`` adds another user's default account and its rows, for the caller-scoping
    claims. Its rows are in the same tables, so a read that lost its ``user_id`` predicate returns
    them.

    ``session_state`` is the session row's own state. It is ``RUNNING`` for every claim about the
    retained endpoints, because a running session is the one whose isolated state a retained
    endpoint must not touch. ``STOPPED`` is passed by the one test that drives the session's OWN
    reset, which is the state the state machine permits ``reset`` from (Requirement 17.14).

    The statement log is cleared before returning: the seeding issues dozens of statements, and a
    test that asserts what a REQUEST issued must not have to skip past them. The migration probe's
    verdict is deliberately left cached, so the request's own statements are only its own reads.
    """
    supabase = _double(_session_row(session_state=session_state))

    default_account = repo.get_or_create_account(
        supabase, USER, repo.DEFAULT_CURRENCY, None, initial_capital=DEFAULT_CAPITAL
    )
    session_account = repo.get_or_create_account(
        supabase, USER, repo.DEFAULT_CURRENCY, SESSION, initial_capital=SESSION_CAPITAL
    )
    _seed_account_set(
        supabase,
        account_id=str(default_account["id"]),
        session_id=None,
        user_id=USER,
        symbol=DEFAULT_SYMBOL,
        marker="default",
    )
    _seed_account_set(
        supabase,
        account_id=str(session_account["id"]),
        session_id=SESSION,
        user_id=USER,
        symbol=SESSION_SYMBOL,
        marker="session",
    )

    if second_tenant:
        foreign = repo.get_or_create_account(
            supabase, OTHER_USER, repo.DEFAULT_CURRENCY, None, initial_capital=DEFAULT_CAPITAL
        )
        _seed_account_set(
            supabase,
            account_id=str(foreign["id"]),
            session_id=None,
            user_id=OTHER_USER,
            symbol=OTHER_TENANT_SYMBOL,
            marker="foreign",
        )

    supabase.statements.clear()
    supabase.ops.clear()
    return supabase


def _default_account_id(supabase: FakeSupabase, user_id: str = USER) -> str:
    """The id of ``user_id``'s ``session_id IS NULL`` account, read off the stored rows."""
    return str(supabase.default_account(user_id)["id"])


def _session_account_id(supabase: FakeSupabase, session_id: str = SESSION) -> str:
    for row in supabase.accounts:
        if str(row.get("session_id")) == session_id:
            return str(row["id"])
    raise AssertionError(f"no account is scoped to session {session_id}")


def _resting_order(supabase: FakeSupabase, session_id: Optional[str]) -> Dict[str, Any]:
    """The ``ACCEPTED`` limit buy :func:`_seed_account_set` left on ``session_id``'s book.

    Read off the stored rows rather than remembered from the seeding, so it is the row as the
    table holds it - which is what an assertion about "untouched" has to compare against.
    """
    for row in supabase.orders:
        if _text_or_none(row.get("session_id")) != session_id:
            continue
        if row.get("order_state") == PaperOrderState.ACCEPTED.value:
            return dict(row)
    raise AssertionError(f"no ACCEPTED order rests on session_id={session_id!r}")


def _account_row(supabase: FakeSupabase, account_id: str) -> Dict[str, Any]:
    for row in supabase.accounts:
        if str(row["id"]) == account_id:
            return dict(row)
    raise AssertionError(f"no account {account_id}")


def _text_or_none(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _frozen_session_config() -> Dict[str, Any]:
    """``paper_sessions.config`` as ``insert_session`` would have written it.

    ``tests/test_task_28_1_session_routes._session_row`` carries a two-key placeholder, which is
    all its own tests need: every reset test there monkeypatches ``reset_session``. The ONE test
    here that drives the real reset needs the real thing, because ``reset_session`` reads the
    recorded configuration and refuses a partial one rather than guessing a rounding mode
    (Requirements 16.12, 28.3) - so it is frozen through ``paper_simulator``'s own two functions,
    not hand-written, and rendered through ``repo.session_config_payload`` the way the INSERT
    renders it.
    """
    metadata = sim.resolve_market_metadata(
        {
            SESSION_ROW_SYMBOL: {
                "symbol": SESSION_ROW_SYMBOL,
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
                "precision": {"price": 0.01, "amount": 0.00000001},
                "limits": {"amount": {"min": 0.0001, "max": 1000.0}},
            }
        },
        exchange_id=EXCHANGE,
        symbol=SESSION_ROW_SYMBOL,
    )
    return repo.session_config_payload(
        sim.freeze_session_config(
            metadata=metadata, currency="USD", market_data_source="MARKET_DATA_SERVICE"
        )
    )


def _lock_the_reservation(
    supabase: FakeSupabase, account_id: str, amount: Decimal, capital: Decimal
) -> None:
    """Move ``amount`` from ``available_balance`` to ``locked_balance`` on ``account_id``.

    :func:`_seed_account_set` writes the resting buy but not the reservation it would have taken,
    and an account with ``locked_balance = 0`` cannot show a leak: ``cancel_order`` releases
    ``min(reserved, locked_balance)``, so a cancel that reached the wrong book would move nothing
    and "the balances did not change" would pass for the wrong reason. Written through
    ``lock_account_for_update`` + ``bump_version`` - the version-guarded UPDATE the repository
    already uses - rather than by mutating a row, so the ``version`` this test compares afterwards
    is a real one.
    """
    before = repo.lock_account_for_update(supabase, USER, account_id=account_id)
    repo.bump_version(
        supabase,
        user_id=USER,
        account_id=account_id,
        expected_version=before["version"],
        payload={
            "available_balance": capital - amount,
            "locked_balance": amount,
            "total_equity": Decimal(str(before["total_equity"])),
        },
    )


def _limiter_key(handler: str) -> str:
    """The key ``slowapi`` files a decorated handler's limits under.

    ``Limiter.limit`` stores them against ``f"{func.__module__}.{func.__name__}"``, so the key is
    derived here the same way rather than written out - and the fact that it is derived from the
    NAME is precisely why :func:`test_no_two_paper_handlers_share_a_limiter_key` matters.
    """
    return f"{paper_trading.__name__}.{handler}"


def _registered_limits(handler: str) -> List[str]:
    return [str(item.limit) for item in limiter._route_limits.get(_limiter_key(handler), [])]


def _concrete(path: str) -> str:
    """One registered path with its parameters filled in, so a matcher can be asked about it."""
    return (
        path.replace("{session_id}", SESSION)
        .replace("{order_id}", "aaaaaaaa-0000-0000-0000-000000000001")
    )


def _full_matches(method: str, url: str) -> List[str]:
    """The names of every registered route that FULLY matches ``method url``.

    Starlette resolves a request by walking ``app.routes`` in registration order and taking the
    first full match, so "exactly one full match" is the strongest available statement that a later
    route cannot shadow an earlier one - and it is a fact about the matcher rather than about a
    table of names.
    """
    from backend_app.main import app

    scope: Dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": url,
        "path_params": {},
        "root_path": "",
        "headers": [],
    }
    matched: List[str] = []
    for route in app.routes:
        verdict, _child = route.matches(scope)
        if verdict.name == "FULL":
            matched.append(str(getattr(route, "name", "")))
    return matched


def _route_for(method: str, path: str) -> Any:
    from backend_app.main import app

    for route in app.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in (getattr(route, "methods", None) or []):
            return route
    raise AssertionError(f"{method} {path} is not registered")


# ══════════════════════════════════════════════════════════════════════════
#  1. THE RETAINED EIGHT STILL RESOLVE TO THEIR OWN HANDLERS (Req 17.12)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetainedEightStillResolveToTheirOwnHandlers:
    """Function identity, and Starlette's own matcher - not a name in a table.

    ``tests/test_task_28_1_session_routes.TestTheRouteTable`` asserts that each retained
    (method, path) carries the expected route NAME. That is necessary and it is not sufficient once
    fourteen paths have been added beside them: a route's name is derived from its endpoint's
    ``__name__``, so two functions sharing a name are indistinguishable by name, and a path added
    ahead of a retained one would shadow it without changing any name at all. Both are checked
    directly here.

    Red run: ``@router.get("/sessions/{session_id}")``'s path was changed to ``/{session_id}``.
    Task 28.1's ``test_the_existing_endpoints_still_resolve_to_their_own_handlers`` still passed -
    all eight retained pairs still resolved to their own names, so the retained endpoints read as
    untouched - while :meth:`test_exactly_one_registered_route_fully_matches_each_retained_url`
    failed for all five retained GETs, each naming ``get_paper_session`` as the second matcher.
    """

    @pytest.mark.parametrize(
        "method,path", sorted(EXISTING_ROUTES), ids=[f"{m} {p}" for m, p in sorted(EXISTING_ROUTES)]
    )
    def test_the_route_holds_the_function_the_module_still_exposes(
        self, method: str, path: str
    ) -> None:
        """The registered endpoint IS ``paper_trading.<name>``, not merely something called that."""
        name, _limit = EXISTING_ROUTES[(method, path)]
        route = _route_for(method, path)
        assert route.endpoint is getattr(paper_trading, name), (
            f"{method} {path} is served by {getattr(route.endpoint, '__qualname__', route.endpoint)!r} "
            f"rather than by paper_trading.{name}; task 28.3 requires the retained endpoints to "
            f"keep their own handlers (Requirement 17.12)"
        )

    @pytest.mark.parametrize(
        "method,path", sorted(EXISTING_ROUTES), ids=[f"{m} {p}" for m, p in sorted(EXISTING_ROUTES)]
    )
    def test_exactly_one_registered_route_fully_matches_each_retained_url(
        self, method: str, path: str
    ) -> None:
        """No added path shadows a retained one, asked of the matcher that will decide it."""
        name, _limit = EXISTING_ROUTES[(method, path)]
        matches = _full_matches(method, _concrete(path))
        assert matches == [name], (
            f"{method} {_concrete(path)} is fully matched by {matches} rather than by {name!r} "
            f"alone. A second full match means a route added by task 28 can shadow a retained "
            f"endpoint, which is the rename Requirement 17.12 forbids in its most silent form"
        )

    @pytest.mark.parametrize("handler", sorted(ADDED_ROUTES))
    def test_no_retained_handler_answers_an_added_url(self, handler: str) -> None:
        """And the converse: a session-scoped URL reaches its own handler and nothing retained.

        The direction matters on its own. ``DELETE /api/paper/orders/{order_id}`` and
        ``GET /api/paper/sessions/{session_id}/orders`` both end in a parameter followed by
        ``orders``, and a retained handler that answered a session URL would serve the default
        account's rows under a session's path - which reads as an empty session rather than as an
        error.
        """
        method, path, _limit = ADDED_ROUTES[handler]
        matches = _full_matches(method, _concrete(path))
        assert matches == [handler], (
            f"{method} {_concrete(path)} is fully matched by {matches} rather than by {handler!r} "
            f"alone"
        )

    def test_the_retained_paths_and_the_added_paths_are_disjoint(self) -> None:
        """Task 28 added fourteen (method, path) pairs; it re-used none of the eight.

        Stated over the tables rather than over the app, because a task that re-declared a retained
        pair would register two routes for it and the first would still answer - so the app would
        look correct while the router held a second handler for a path that already had one.
        """
        retained = set(EXISTING_ROUTES)
        added = {(method, path) for method, path, _ in ADDED_ROUTES.values()}
        assert retained & added == set(), (
            f"task 28 declared a route on a retained (method, path): {sorted(retained & added)}"
        )
        assert len(added) == 14, f"task 28 declares fourteen routes, not {len(added)}"

    def test_the_twenty_two_handlers_are_twenty_two_distinct_functions(self) -> None:
        """No added handler took a retained handler's name, and none took another's.

        This is the mechanism behind both of the failures the two tests above look for, and behind
        the limiter-key merge in section 2: the router module is a namespace, and a second
        ``async def get_paper_orders`` in it would silently replace the first.
        """
        names = [name for name, _ in EXISTING_ROUTES.values()] + list(ADDED_ROUTES)
        assert len(names) == len(set(names)) == 22, (
            f"the twenty-two paper handlers do not have twenty-two distinct names: {sorted(names)}"
        )
        functions = {name: getattr(paper_trading, name, None) for name in names}
        missing = sorted(name for name, func in functions.items() if func is None)
        assert missing == [], f"paper_trading no longer defines: {missing}"
        assert len({id(func) for func in functions.values()}) == 22, (
            "two paper route handlers are the same function object, which means one name was "
            "bound twice and one endpoint is unreachable"
        )


# ══════════════════════════════════════════════════════════════════════════
#  2. THE RATE LIMITS THAT WILL ACTUALLY BE ENFORCED (Requirement 22.4)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRateLimitsAreTheOnesThatWillBeEnforced:
    """120/60s reads, 60/60s writes, 30/60s reset - read out of the limiter, not out of the source.

    ``tests/test_task_28_1_session_routes`` asserts the decorator as it is WRITTEN, by parsing the
    router. That catches an edit to the file and cannot catch anything else. ``slowapi`` files each
    decorated handler's limits in ``Limiter._route_limits`` under ``module.function``, and that
    registry is what the middleware consults per request - so a handler whose limit was registered
    under another key, or whose key collected a second limit, would pass the source assertion and
    be throttled at a figure nobody wrote down.

    Red run: a second ``@limiter.limit("10/minute")`` was added to ``get_paper_account``.
    ``test_each_existing_endpoint_keeps_its_rate_limit`` in task 28.1 still passed - the 120/minute
    decorator was still there - and :meth:`test_no_retained_endpoint_collected_a_second_limit`
    failed naming the handler and both limits.
    """

    #: ``slowapi`` renders ``"120/minute"`` as ``"120 per 1 minute"``. Translated rather than
    #: hard-coded twice, so the tables imported from task 28.1 stay the only place a figure is
    #: written.
    @staticmethod
    def _as_registered(limit: str) -> str:
        count, _, period = limit.partition("/")
        return f"{count} per 1 {period}"

    @pytest.mark.parametrize("handler", sorted({name for name, _ in EXISTING_ROUTES.values()}))
    def test_each_retained_endpoint_is_registered_with_its_own_limit(self, handler: str) -> None:
        limit = next(lim for name, lim in EXISTING_ROUTES.values() if name == handler)
        assert _registered_limits(handler) == [self._as_registered(limit)], (
            f"{handler} will be limited at {_registered_limits(handler)} rather than at {limit!r}; "
            f"task 28.3 requires the retained 120/60s reads, 60/60s writes and 30/60s reset "
            f"(Requirement 22.4)"
        )

    @pytest.mark.parametrize("handler", sorted(ADDED_ROUTES))
    def test_each_added_route_is_registered_with_its_own_limit(self, handler: str) -> None:
        """The added fourteen, for the same reason: an unlimited new route is Requirement 22.4's gap.

        Asserted here rather than in tasks 28.1 and 28.2 because those two read the source and this
        reads the registry - one claim per file, and the registry is where a collision between the
        two halves of task 28 would show.
        """
        _method, _path, limit = ADDED_ROUTES[handler]
        assert _registered_limits(handler) == [self._as_registered(limit)], (
            f"{handler} will be limited at {_registered_limits(handler)} rather than at {limit!r}"
        )

    @pytest.mark.parametrize("handler", sorted({name for name, _ in EXISTING_ROUTES.values()}))
    def test_no_retained_endpoint_collected_a_second_limit(self, handler: str) -> None:
        """One key, one limit. Two would mean the stricter of them silently applies."""
        registered = _registered_limits(handler)
        assert len(registered) == 1, (
            f"{handler} carries {len(registered)} registered limits ({registered}); a retained "
            f"endpoint that collected a second one is throttled at a figure task 28.3 does not name"
        )

    def test_no_two_paper_handlers_share_a_limiter_key(self) -> None:
        """The registry is keyed by name, so twenty-two handlers must own twenty-two keys.

        If an added handler had been given a retained handler's name, ``slowapi`` would have filed
        both limits under the one key and the retained endpoint would inherit the added route's
        cap - the session start's 10/60s on ``GET /api/paper/account``, for instance.
        """
        handlers = [name for name, _ in EXISTING_ROUTES.values()] + list(ADDED_ROUTES)
        keys = {_limiter_key(handler) for handler in handlers}
        assert len(keys) == len(handlers) == 22
        unregistered = sorted(key for key in keys if key not in limiter._route_limits)
        assert unregistered == [], (
            f"these paper handlers are not rate limited at all: {unregistered} (Requirement 22.4)"
        )

    def test_a_retained_limit_is_armed_and_not_merely_declared(self) -> None:
        """The 61st ``DELETE /api/paper/orders/{id}`` in a minute is refused.

        The cheapest retained endpoint to exhaust: the cancel reads one order, finds none and
        answers 400, so sixty requests cost sixty single-row reads. What is being asserted is not
        the 400 - it is that the sixty-first request is answered 429 by the decorator before the
        handler runs, which is the only way to show the limiter is armed in this environment rather
        than configured and bypassed.
        """
        supabase = _both_accounts()
        client = _client(supabase)
        unknown = "/api/paper/orders/aaaaaaaa-0000-0000-0000-00000000ffff"

        codes = [client.delete(unknown).status_code for _ in range(61)]

        assert 429 not in codes[:60], (
            f"a retained write was refused before its 60/60s allowance was spent: "
            f"{codes[:60].index(429) + 1} requests in"
        )
        assert codes[60] == 429, (
            f"the sixty-first DELETE in a minute was answered {codes[60]}; the retained 60/60s "
            f"write limit is declared but not enforced (Requirement 22.4)"
        )

    def test_exhausting_the_session_start_does_not_throttle_a_retained_write(self) -> None:
        """The two counters are separate, which is what "additive" has to mean for a rate limit.

        ``POST /api/paper/sessions`` is capped at 10/60s and ``POST /api/paper/orders`` at 60/60s.
        A shared counter - one key for both, or a limit registered on the router rather than on the
        handler - would let a client that opened ten sessions be refused when it next places an
        order, which removes capacity from a retained endpoint without touching its path.
        """
        supabase = _both_accounts()
        client = _client(supabase)

        # A malformed strategy reference, so the ten attempts are refused 422 by the handler
        # WITHOUT entering the pipeline - which would load an exchange's markets over the network.
        # The refusal still spends the allowance, because the limiter's decorator wraps the handler
        # and ``_parse_session_start`` is called inside its body rather than as a dependency.
        refused = _start_body(listing_id="not-a-uuid")
        starts = [
            client.post("/api/paper/sessions", json=refused).status_code for _ in range(11)
        ]
        assert starts[:10] == [422] * 10, starts
        assert starts[10] == 429, (
            f"the session start's 10/60s cap was not reached, so this test proves nothing: {starts}"
        )

        placed = client.post(
            "/api/paper/orders",
            json={
                "symbol": DEFAULT_SYMBOL,
                "side": "buy",
                "order_type": "limit",
                "quantity": 0.01,
                "price": 100.0,
            },
        )
        assert placed.status_code != 429, (
            "a retained write was refused because the session start's allowance was spent; the "
            "two endpoints must not share a rate-limit counter (Requirement 22.4)"
        )
        assert placed.status_code == 200, placed.text


# ══════════════════════════════════════════════════════════════════════════
#  3. THE RETAINED BODIES AND PARAMETERS (Requirements 17.12, 22.2)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetainedBodiesWereNotRetyped:
    """The two request models, field for field - and the ``forbid`` that was not retrofitted.

    Requirement 17.12 permits only additive extension, and a REQUEST body tightens in the opposite
    direction from a response body: adding a required field, narrowing a type, or refusing an
    unknown field all break a caller that was correct yesterday. ``PaperSessionStartRequest``
    declares ``extra="forbid"`` because it is new and has no callers; applying the same to
    ``PaperOrderRequest`` would refuse every request from a client that sends one field the server
    does not know.

    Red run: ``model_config = ConfigDict(extra="forbid")`` was added to ``PaperOrderRequest``.
    :meth:`test_the_retained_order_route_still_accepts_an_unknown_field` failed with a 422 - which
    is exactly the break an ``api.paper`` client would have shipped into.
    """

    @pytest.mark.parametrize(
        "model_name,frozen",
        [
            ("PaperOrderRequest", RETAINED_ORDER_BODY),
            ("PaperResetRequest", RETAINED_RESET_BODY),
        ],
    )
    def test_the_model_keeps_its_fields_types_and_defaults(
        self, model_name: str, frozen: Dict[str, Tuple[Any, bool, Any]]
    ) -> None:
        model = getattr(paper_trading, model_name)
        assert set(model.model_fields) == set(frozen), (
            f"{model_name}'s field set changed: unexpected "
            f"{sorted(set(model.model_fields) - set(frozen))}, absent "
            f"{sorted(set(frozen) - set(model.model_fields))}"
        )
        for name, (annotation, required, default) in frozen.items():
            field = model.model_fields[name]
            assert field.annotation == annotation, (
                f"{model_name}.{name} is now {field.annotation!r} rather than {annotation!r}; "
                f"a retyped request field is the retyping Requirement 17.12 forbids"
            )
            assert field.is_required() is required, (
                f"{model_name}.{name} is {'now' if required else 'no longer'} required, which "
                f"changes what an existing caller must send"
            )
            if not required:
                assert field.default == default, (
                    f"{model_name}.{name} now defaults to {field.default!r} rather than to "
                    f"{default!r}"
                )

    @pytest.mark.parametrize("model_name", ["PaperOrderRequest", "PaperResetRequest"])
    def test_the_session_starts_forbid_was_not_retrofitted(self, model_name: str) -> None:
        model = getattr(paper_trading, model_name)
        assert model.model_config.get("extra") != "forbid", (
            f"{model_name} now forbids extra fields. The session start does, because it is new; "
            f"applying it to a retained body refuses a request that was valid before this task "
            f"(Requirement 17.12)"
        )
        assert (
            paper_trading.PaperSessionStartRequest.model_config.get("extra") == "forbid"
        ), "the session start must still forbid extra fields, or this contrast means nothing"

    def test_the_retained_order_route_still_accepts_an_unknown_field(self) -> None:
        """Stated as a request rather than as a config value, because that is what a client sees."""
        supabase = _both_accounts()
        client = _client(supabase)

        response = client.post(
            "/api/paper/orders",
            json={
                "symbol": DEFAULT_SYMBOL,
                "side": "buy",
                "order_type": "limit",
                "quantity": 0.01,
                "price": 100.0,
                "listing_id": LISTING,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["session_id"] is None, (
            "an unknown field must be ignored, not read: a ``listing_id`` on the retained route "
            "does not make the order a session's"
        )

    @pytest.mark.parametrize("handler", sorted(RETAINED_PARAMETERS))
    def test_the_retained_endpoint_declares_the_parameters_it_always_did(
        self, handler: str
    ) -> None:
        """Asserted against the resolved dependant, so a parameter is what FastAPI will read.

        In particular no retained endpoint declares ``session_id``: a retained read that accepted
        one would answer a session's rows under a path whose documented meaning is the default
        account's, which changes a field's meaning without changing its name.
        """
        method, path = next(
            (m, p) for (m, p), (name, _l) in EXISTING_ROUTES.items() if name == handler
        )
        route = _route_for(method, path)
        declared = {
            field.name
            for field in list(route.dependant.query_params) + list(route.dependant.path_params)
        }
        assert declared == set(RETAINED_PARAMETERS[handler]), (
            f"{handler} declares {sorted(declared)} rather than "
            f"{sorted(RETAINED_PARAMETERS[handler])}; task 28.3 adds routes and retypes nothing"
        )


class TestTheRetainedBodyCannotReachTheSessionRoute:
    """``extra="forbid"`` from the boundary's side: the old body is refused, and not echoed.

    Task 28.1 proves the refusal over twelve fields a caller might smuggle Protected_Logic or an
    identity through. These six are different: they are the fields the RETAINED bodies declare, so
    a client that pointed its existing order-placement payload at the new session route would send
    them. The value-free 422 is what Requirement 22.2 asks for either way, and the six are checked
    here because the boundary is what task 28.3 is about.

    Red run: ``_parse_session_start``'s allow-list check was widened to ignore unknown fields.
    Every case here failed on the status code, and the ``capital`` case then reached the pipeline
    with a field the session service has no meaning for.
    """

    def test_the_two_refusal_tables_do_not_overlap(self) -> None:
        """A field asserted in both files would be one claim recorded twice."""
        theirs = {field for field, _value in UNACCEPTED_FIELDS}
        mine = set(RETAINED_BODY_FIELDS_THE_SESSION_START_REFUSES)
        assert theirs & mine == set(), (
            f"these fields are already covered by test_task_28_1_session_routes: "
            f"{sorted(theirs & mine)}"
        )
        accepted = set(paper_trading.PaperSessionStartRequest.model_fields)
        assert mine & accepted == set(), (
            f"the session start ACCEPTS {sorted(mine & accepted)}, so refusing them is not the "
            f"claim to make about them"
        )

    @pytest.mark.parametrize("field", RETAINED_BODY_FIELDS_THE_SESSION_START_REFUSES)
    def test_a_retained_body_field_is_refused_without_its_value(self, field: str) -> None:
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body(**{field: SMUGGLED}))

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == paper_trading.PAPER_SESSION_REQUEST_INVALID
        assert field in detail["unexpected_fields"], detail
        assert SMUGGLED not in response.text, (
            f"the 422 echoed the value supplied for {field!r}; a refusal must name the field and "
            f"repeat no supplied value (Requirement 22.2)"
        )

    def test_a_session_start_body_posted_to_the_retained_order_route_creates_no_session(
        self,
    ) -> None:
        """And the other direction: the new body does not open a session through the old path."""
        supabase = _both_accounts()
        client = _client(supabase)

        response = client.post(
            "/api/paper/orders",
            json={
                "listing_id": LISTING,
                "symbol": DEFAULT_SYMBOL,
                "timeframe": "1m",
                "initial_capital_minor": CAPITAL_MINOR,
                "currency": "USD",
            },
        )

        assert response.status_code == 422, response.text
        assert [row["id"] for row in supabase.sessions] == [SESSION], (
            "a session was created through the retained order route"
        )
        assert supabase.statements_on(repo.SESSIONS_TABLE, "insert") == []


# ══════════════════════════════════════════════════════════════════════════
#  4. DEFAULT-ACCOUNT SEMANTICS: ``session_id IS NULL`` (Requirement 17.12)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetainedReadsStillAnswerFromTheDefaultAccount:
    """A RUNNING session with its own account, orders, position, fill and ledger changes nothing.

    ``tests/test_paper_api_shape_compatibility.py`` asserts these bodies with no session in
    existence, which is the only state that was possible before task 28. The premise here is the
    new one: the caller owns a session whose rows sit in the SAME tables, and the retained reads
    must still answer from the ``session_id IS NULL`` account. A read that lost its account
    predicate would report a session's balances as the trader's own, which is Requirement 17.6's
    isolation and Requirement 17.12's unchanged meaning failing together.

    Red run: ``PaperTradingService.get_orders`` had ``account_id=account["id"]`` removed from its
    ``repo.get_orders`` call. Three cases failed - this class's ``/orders`` case, the
    ``?session_id=`` case below it and section 5's cross-tenant case - each with the session's
    ``ETH-USDT`` order in a default-account body. The response SHAPE did not move: the extra rows
    carry the same keys and the same types as the ones that belong there, and the frozen baseline's
    scenario has no session in it at all, so nothing in
    ``tests/test_paper_api_shape_compatibility.py`` could have caught it.
    """

    def test_the_account_is_located_by_session_id_is_null(self) -> None:
        """The spelling matters: ``.eq("session_id", None)`` renders as ``eq.None`` and matches
        nothing, which would create a second default account rather than find the one there is."""
        supabase = _both_accounts()
        client = _client(supabase)

        response = client.get("/api/paper/account")

        assert response.status_code == 200, response.text
        reads = [
            statement
            for statement in supabase.statements_on(repo.ACCOUNTS_TABLE, "select")
            if statement.filters
        ]
        assert reads, "the account read issued no predicated statement"
        assert ("is", "session_id", "null") in reads[0].filters, (
            f"the retained account read is not scoped to the default account; its predicates are "
            f"{reads[0].filters}"
        )
        assert reads[0].filter_value("user_id") == USER

    def test_the_reported_capital_is_the_default_accounts(self) -> None:
        supabase = _both_accounts()
        client = _client(supabase)

        account = client.get("/api/paper/account").json()

        assert account["account_id"] == _default_account_id(supabase)
        assert Decimal(account["initial_capital"]) == DEFAULT_CAPITAL, (
            f"the retained account read reported {account['initial_capital']}, which is the "
            f"session account's capital ({SESSION_CAPITAL}) rather than the default account's"
        )

    @pytest.mark.parametrize("path,key", RETAINED_COLLECTION_READS)
    def test_only_the_default_accounts_rows_are_served(self, path: str, key: str) -> None:
        supabase = _both_accounts()
        client = _client(supabase)

        response = client.get(path)

        assert response.status_code == 200, response.text
        body = response.json()
        symbols = {row["symbol"] for row in body[key]}
        assert symbols == {DEFAULT_SYMBOL}, (
            f"{path} served {sorted(symbols)}; the session's rows belong to the session's account "
            f"and must not appear in the default account's read (Requirements 17.6, 17.12)"
        )
        assert body[key], f"{path} returned nothing, so this assertion proves nothing"

    def test_the_summary_counts_only_the_default_accounts_fills(self) -> None:
        """``total_trades`` counts fills, and the session's fill is not one of them.

        ``paper_fills`` carries no ``account_id`` - it reaches the account through its order - so
        this read is scoped by ``session_id IS NULL`` directly. Both accounts hold exactly one
        fill, so a lost predicate reports two.
        """
        supabase = _both_accounts()
        client = _client(supabase)

        summary = client.get("/api/paper/summary").json()

        assert summary["account"]["account_id"] == _default_account_id(supabase)
        assert summary["total_trades"] == 1, (
            f"the summary counted {summary['total_trades']} fills; the default account has one and "
            f"the session has one, and only the first is this endpoint's"
        )
        assert summary["open_positions_count"] == 1

    @pytest.mark.parametrize(
        "path,key",
        list(RETAINED_COLLECTION_READS) + [("/api/paper/account", None), ("/api/paper/summary", None)],
    )
    def test_the_envelope_still_reports_a_null_session_id(
        self, path: str, key: Optional[str]
    ) -> None:
        """The additive marker of task 23.3, asserted while a session actually exists.

        ``tests/test_paper_api_shape_compatibility.py`` asserts ``session_id is None`` on three of
        these bodies in a process where no Paper_Session has ever been created - which is a weaker
        premise than this one, because a hard-coded ``None`` and a correctly derived one are
        indistinguishable when there is nothing else it could be.
        """
        supabase = _both_accounts()
        client = _client(supabase)

        body = client.get(path).json()

        assert body["session_id"] is None, (
            f"{path} reports session_id={body['session_id']!r}. The default account is not a "
            f"Paper_Session, and a retained consumer reads this field to know that"
        )
        assert body["execution_environment"] == PAPER_EXECUTION_ENVIRONMENT
        assert body["is_simulated"] is True
        if key is not None:
            for row in body[key]:
                assert row.get("session_id") is None, (
                    f"a {path} element reports a session id; every row it serves belongs to the "
                    f"default account"
                )

    def test_a_session_id_query_parameter_is_not_a_way_into_a_session(self) -> None:
        """An undeclared query parameter is ignored, and ignoring it is the whole point.

        FastAPI drops a parameter no handler declares, so ``?session_id=`` cannot re-scope a
        retained read. Asserted because it is the first thing a caller who has read task 28.2's
        paths will try, and because a future handler that added the parameter "for convenience"
        would turn a retained path into a session read.
        """
        supabase = _both_accounts()
        client = _client(supabase)

        body = client.get(f"/api/paper/orders?session_id={SESSION}").json()

        assert {row["symbol"] for row in body["orders"]} == {DEFAULT_SYMBOL}
        assert body["session_id"] is None
        read = supabase.statements_on(repo.ORDERS_TABLE, "select")[0]
        assert read.filter_value("account_id") == _default_account_id(supabase)
        assert "session_id" not in read.filtered_columns(), (
            "the retained orders read narrowed by session_id, which it has never done: the "
            "default account is identified by its account_id here"
        )


class TestTheRetainedWritesStillTouchOnlyTheDefaultAccount:
    """A reset and an order placement, with a session's rows sitting beside them untouched.

    Red run: ``reset_account``'s ``repo.get_orders(..., account_id=account_id)`` had its account
    predicate removed. :meth:`test_the_reset_leaves_the_sessions_orders_and_position_alone` failed
    with the session's resting order ``CANCELLED`` - a retained endpoint reaching into a
    Paper_Session's isolated state, which Requirement 17.6 forbids and which no shape assertion
    would have noticed.
    """

    def test_the_reset_leaves_the_sessions_orders_and_position_alone(self) -> None:
        supabase = _both_accounts()
        session_account = _session_account_id(supabase)
        client = _client(supabase)

        response = client.post("/api/paper/account/reset", json={"capital": 25_000.0})

        assert response.status_code == 200, response.text
        assert response.json()["session_id"] is None

        session_orders = [
            row for row in supabase.orders if str(row.get("account_id")) == session_account
        ]
        assert session_orders, "the seeding wrote no session orders, so this proves nothing"
        assert {row["order_state"] for row in session_orders} == {
            PaperOrderState.ACCEPTED.value,
            PaperOrderState.FILLED.value,
        }, (
            "the retained reset moved a Paper_Session's orders; it resets the default account "
            "(Requirements 17.6, 17.12)"
        )

        session_positions = [
            row for row in supabase.positions if str(row.get("account_id")) == session_account
        ]
        assert [
            (Decimal(row["size"]), row["closed_at"]) for row in session_positions
        ] == [(Decimal("1"), None)], (
            f"the retained reset closed a Paper_Session's position: {session_positions}"
        )

        session_row = [row for row in supabase.accounts if str(row["id"]) == session_account][0]
        assert Decimal(session_row["initial_capital"]) == SESSION_CAPITAL
        assert Decimal(session_row["available_balance"]) == SESSION_CAPITAL

    def test_the_reset_reports_the_default_accounts_new_capital(self) -> None:
        supabase = _both_accounts()
        client = _client(supabase)

        body = client.post("/api/paper/account/reset", json={"capital": 25_000.0}).json()

        assert body["account"]["account_id"] == _default_account_id(supabase)
        assert Decimal(body["account"]["available_balance"]) == Decimal("25000")

    def test_a_placed_order_belongs_to_the_default_account(self) -> None:
        supabase = _both_accounts()
        default_account = _default_account_id(supabase)
        client = _client(supabase)

        response = client.post(
            "/api/paper/orders",
            json={
                "symbol": DEFAULT_SYMBOL,
                "side": "buy",
                "order_type": "limit",
                "quantity": 0.01,
                "price": 100.0,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["session_id"] is None
        placed = supabase.statements_on(repo.ORDERS_TABLE, "insert")
        assert len(placed) == 1, placed
        assert placed[0].payload["account_id"] == default_account
        assert placed[0].payload["session_id"] is None, (
            "the retained placement wrote a session-scoped order; it places into the default "
            "account (Requirement 17.12)"
        )


class TestTheRetainedCancelIsScopedToTheDefaultAccount:
    """``DELETE /api/paper/orders/{id}`` is about ONE book, and it is the ``session_id IS NULL`` one.

    This is the defect this module reported (see the header) and the assertion that closes it.
    ``cancel_order`` read its target by ``(user_id, order_id)`` alone, so a caller holding one of
    their own session's order ids - which ``GET /api/paper/sessions/{id}/orders`` hands them -
    could move a session order to ``CANCELLED`` through the retained path, release locked capital
    on the SESSION's account, and leave the session's ``paper_events`` log with no record that any
    of it happened. Three things were wrong with that at once: the Paper_Session state machine
    ``paper_session_service`` owns was bypassed, the session's audit trail lost a money movement
    (Requirements 17.7, 19.2), and the retained endpoint stopped having the default-account
    semantics Requirement 17.12 documents for it.

    The fix NARROWS the read (``repo.read_order(..., session_id=None)``). It widens nothing: the
    ``user_id`` predicate is untouched, and a default-account order cancels exactly as it always
    did - which is what :meth:`test_the_default_accounts_order_still_cancels` is here to hold.

    Red run: with ``repo.read_order(sb, uid, order_id)`` restored in
    ``paper_trading_service.cancel_order``,
    :meth:`test_a_session_order_id_answers_exactly_as_an_unknown_one_does` failed with
    ``assert 200 == 400`` and
    :meth:`test_the_sessions_order_and_its_locked_capital_are_untouched` failed with the session's
    order at ``CANCELLED`` and ``locked_balance`` back to ``0`` on the session's account - the leak,
    stated as a failure.
    """

    def test_a_session_order_id_answers_exactly_as_an_unknown_one_does(self) -> None:
        """Same status, same code, same message - and no field the caller did not supply.

        Requirement 21.4: answering "forbidden", or "not cancellable", or anything else that
        differs from the unknown-id answer would confirm the order exists, which is the disclosure
        ``read_order``'s own docstring argues against.
        """
        supabase = _both_accounts()
        session_order = _resting_order(supabase, SESSION)
        client = _client(supabase)

        leaked = client.delete(f"/api/paper/orders/{session_order['id']}")
        unknown = client.delete(f"/api/paper/orders/{UNKNOWN_ORDER}")

        assert leaked.status_code == 400, (
            f"a session-scoped order id was accepted by the retained cancel: {leaked.text}. "
            f"The retained endpoints are about the default account (Requirement 17.12) and a "
            f"session's orders move through its own reset (Requirement 17.15)"
        )
        assert leaked.status_code == unknown.status_code
        assert leaked.json()["detail"] == {
            "error": "CANCEL_FAILED",
            "message": f"Order {session_order['id']} not found",
        }
        assert unknown.json()["detail"] == {
            "error": "CANCEL_FAILED",
            "message": f"Order {UNKNOWN_ORDER} not found",
        }
        assert SESSION not in leaked.text, (
            f"the refusal names the session the order belongs to: {leaked.text}"
        )
        assert str(session_order["account_id"]) not in leaked.text, (
            f"the refusal names the account the order belongs to: {leaked.text}"
        )

    def test_the_sessions_order_and_its_locked_capital_are_untouched(self) -> None:
        """The refusal writes nothing at all - not the order, not the balances, not an event."""
        supabase = _both_accounts()
        session_account = _session_account_id(supabase)
        _lock_the_reservation(
            supabase, session_account, RESERVED_ON_THE_RESTING_BUY, SESSION_CAPITAL
        )
        session_order = _resting_order(supabase, SESSION)
        before_order = dict(session_order)
        before_account = _account_row(supabase, session_account)
        # The seeding's own ``FILL`` ledger row is already there; what must not appear is a new one.
        before_ledger = list(supabase.balance_events)
        supabase.statements.clear()
        supabase.ops.clear()
        client = _client(supabase)

        response = client.delete(f"/api/paper/orders/{session_order['id']}")

        assert response.status_code == 400, response.text

        after_order = next(
            dict(row) for row in supabase.orders if str(row["id"]) == str(session_order["id"])
        )
        assert after_order["order_state"] == PaperOrderState.ACCEPTED.value, (
            "the retained cancel moved a Paper_Session's order out of ACCEPTED; only "
            "paper_session_service moves a session's orders (Requirement 17.15)"
        )
        assert after_order["legacy_status"] == "OPEN"
        assert Decimal(str(after_order["filled_quantity"])) == Decimal("0")
        assert after_order == before_order, (
            f"the session's order row changed: {before_order} -> {after_order}"
        )

        after_account = _account_row(supabase, session_account)
        assert Decimal(after_account["locked_balance"]) == RESERVED_ON_THE_RESTING_BUY, (
            "the retained cancel released capital locked on a Paper_Session's account "
            "(Requirements 17.6, 17.12)"
        )
        assert Decimal(after_account["available_balance"]) == (
            SESSION_CAPITAL - RESERVED_ON_THE_RESTING_BUY
        )
        assert after_account["version"] == before_account["version"], (
            "the session's account was written by a refused cancel"
        )

        assert supabase.balance_events == before_ledger, (
            "a refused cancel wrote a ledger row; the session's balances did not move and "
            "nothing must say they did"
        )
        assert supabase.events == [], (
            f"the retained cancel wrote paper_events rows: {supabase.events}. Its own audit trail "
            f"is paper_balance_events; a session's is paper_events, and this path writes neither "
            f"for an order it refuses"
        )
        assert not supabase.wrote_anything(), (
            f"a refused cancel issued a write: "
            f"{[(s.table_name, s.op) for s in supabase.statements if s.op != 'select']}"
        )

    def test_the_default_accounts_order_still_cancels(self) -> None:
        """The narrowing must not have cost the endpoint the thing it is for."""
        supabase = _both_accounts()
        default_account = _default_account_id(supabase)
        _lock_the_reservation(
            supabase, default_account, RESERVED_ON_THE_RESTING_BUY, DEFAULT_CAPITAL
        )
        order = _resting_order(supabase, None)
        before_ledger = len(supabase.balance_events)
        client = _client(supabase)

        response = client.delete(f"/api/paper/orders/{order['id']}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "cancelled"
        assert body["order"]["status"] == "CANCELLED"
        assert body["session_id"] is None
        assert body["execution_environment"] == PAPER_EXECUTION_ENVIRONMENT
        assert body["is_simulated"] is True

        after_order = next(
            dict(row) for row in supabase.orders if str(row["id"]) == str(order["id"])
        )
        assert after_order["order_state"] == PaperOrderState.CANCELLED.value
        assert after_order["session_id"] is None

        after_account = _account_row(supabase, default_account)
        assert Decimal(after_account["locked_balance"]) == Decimal("0"), (
            "the cancel of a default-account order must still release what it locked "
            "(Requirement 18.4)"
        )
        assert Decimal(after_account["available_balance"]) == DEFAULT_CAPITAL
        # Exactly one new ledger row, and it says what moved. The seeding's own rows are before it.
        assert [
            event["cause"] for event in supabase.balance_events[before_ledger:]
        ] == ["ORDER_UNLOCK"]
        assert str(supabase.balance_events[-1]["account_id"]) == default_account

    def test_the_cancel_locates_its_target_by_session_id_is_null(self) -> None:
        """The spelling, for the reason ``read_account`` and ``probe_idempotency_key`` state it.

        ``.eq("session_id", None)`` renders as ``session_id=eq.None`` and matches nothing, so a
        cancel written that way would report EVERY default-account order as absent - a refusal that
        looks exactly like the correct refusal here, and is a broken endpoint.
        """
        supabase = _both_accounts()
        order = _resting_order(supabase, None)
        client = _client(supabase)

        client.delete(f"/api/paper/orders/{order['id']}")

        read = supabase.statements_on(repo.ORDERS_TABLE, "select")[0]
        assert ("is", "session_id", "null") in read.filters, (
            f"the retained cancel does not narrow to the default account; its filters are "
            f"{read.filters}"
        )
        assert ("eq", "session_id", None) not in read.filters
        assert read.filter_value("user_id") == USER
        assert read.filter_value("id") == str(order["id"])

    def test_the_sessions_own_reset_still_cancels_the_sessions_order(self) -> None:
        """Where Requirement 17.15 puts the capability, exercised end to end over the double.

        Not a restatement of ``tests/test_task_27_session_service.py``'s reset assertions: those
        call the service directly, and the claim here is that the ROUTE the session's orders are
        supposed to move through still moves them - which is what makes the narrowing above a
        redirection rather than a removal. The ``paper_events`` row is the half the retained path
        never wrote.
        """
        supabase = _both_accounts(session_state="STOPPED")
        # The recorded configuration is a PREMISE of the reset, not something it writes, so it is
        # seeded onto the row here rather than left as the placeholder every other test in this
        # module is content with. See :func:`_frozen_session_config`.
        supabase.sessions[0]["config"] = _frozen_session_config()
        session_order = _resting_order(supabase, SESSION)
        client = _client(supabase)

        response = client.post(f"/api/paper/sessions/{SESSION}/reset")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cancelled_orders"] == [str(session_order["id"])]
        assert body["to_state"] == "CREATED"

        after_order = next(
            dict(row) for row in supabase.orders if str(row["id"]) == str(session_order["id"])
        )
        assert after_order["order_state"] == PaperOrderState.CANCELLED.value
        assert after_order["session_id"] == SESSION

        assert [str(event.get("session_id")) for event in supabase.events] == [SESSION], (
            f"the session's reset wrote no paper_events row: {supabase.events} "
            f"(Requirements 17.7, 19.2)"
        )


class TestASessionSubResourceNeverServesADefaultAccountRow:
    """The converse of section 4, which no other module states.

    ``tests/test_task_28_2_session_subresources.test_a_row_of_another_session_is_never_fetched``
    covers a SIBLING session's row - one with a different, non-null ``session_id``. A
    default-account row is the other case, and it is a different one: its ``session_id`` is NULL,
    and a predicate written ``.eq("session_id", value)`` excludes it while a predicate accidentally
    written as "not another session's" would not.

    Red run: ``get_paper_session_orders`` passed ``session_id=None`` to ``get_orders`` instead of
    the gate's ``sid``. ``/sessions/{id}/orders`` then answered with the default account's
    ``BTC-USDT`` order beside the session's own, and both ``orders`` cases here failed - the first
    on the symbol, the second because the statement carried no ``session_id`` predicate at all.
    """

    @pytest.mark.parametrize("segment", SUBRESOURCES_WITH_BOTH_SETS)
    def test_the_default_accounts_row_is_not_in_the_session_read(self, segment: str) -> None:
        supabase = _both_accounts()
        client = _client(supabase)

        response = client.get(f"/api/paper/sessions/{SESSION}/{segment}")

        assert response.status_code == 200, response.text
        body = response.json()
        rows = body[segment]
        assert rows, f"/{segment} returned nothing, so this assertion proves nothing"
        symbols = {row.get("symbol") for row in rows if "symbol" in row}
        if symbols:
            assert symbols == {SESSION_SYMBOL}, (
                f"/sessions/{{id}}/{segment} served {sorted(symbols)}; a default-account row "
                f"carries session_id NULL and belongs to no session"
            )
        assert all(row.get("session_id") in (None, SESSION) for row in rows)
        assert body["session_id"] == SESSION

    @pytest.mark.parametrize("segment", SUBRESOURCES_WITH_BOTH_SETS)
    def test_the_session_is_a_predicate_and_not_a_post_filter(self, segment: str) -> None:
        """Requirement 21.5, on the case the default account introduces.

        The statement must NARROW to the session. A read that fetched both sets and dropped the
        NULL ones afterwards would answer identically here and would have read another scope's
        rows to do it.
        """
        table = {
            "orders": repo.ORDERS_TABLE,
            "positions": repo.POSITIONS_TABLE,
            "fills": repo.FILLS_TABLE,
        }[segment]
        supabase = _both_accounts()
        client = _client(supabase)

        client.get(f"/api/paper/sessions/{SESSION}/{segment}")

        read = supabase.statements_on(table, "select")[0]
        assert read.filter_value("session_id") == SESSION
        assert read.filter_value("user_id") == USER


# ══════════════════════════════════════════════════════════════════════════
#  5. THE RETAINED EIGHT ARE CALLER-SCOPED TOO (Requirements 21.1, 21.5)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRetainedEndpointsAreScopedByTheAuthenticatedIdentity:
    """The identity the dependency produced, as a predicate on the statement.

    ``tests/test_paper_repository.py`` proves that every repository function applies ``user_id`` as
    a predicate. What that cannot show is that the retained HANDLERS pass the authenticated
    identity into it rather than something from the request - and task 28.1 makes that claim only
    for the seven session routes. This is the same claim for the eight retained ones, which is what
    task 28.3 means by "the caller scoping".

    Red run: ``get_paper_orders`` was given a ``user_id: Optional[str] = Query(None)`` parameter and
    passed ``user_id or user["id"]`` to the service.
    :meth:`test_no_retained_route_accepts_an_identity_parameter` failed naming the handler and the
    parameter, and ``test_the_retained_endpoint_declares_the_parameters_it_always_did`` failed
    beside it - the retained read had gained a way to be asked about somebody else.
    """

    @pytest.mark.parametrize("handler", sorted({name for name, _ in EXISTING_ROUTES.values()}))
    def test_the_route_depends_on_get_current_user(self, handler: str) -> None:
        """Against the resolved dependency graph: a deleted ``Depends`` with a live import reads
        correctly in the file and is never called."""
        from backend_app.core.dependencies import get_current_user

        method, path = next(
            (m, p) for (m, p), (name, _l) in EXISTING_ROUTES.items() if name == handler
        )
        route = _route_for(method, path)
        collected: List[Any] = []
        pending = list(route.dependant.dependencies)
        while pending:
            dependency = pending.pop()
            collected.append(getattr(dependency, "call", None))
            pending.extend(getattr(dependency, "dependencies", []))
        assert get_current_user in collected, (
            f"{handler} does not depend on get_current_user (Requirement 22.1)"
        )

    def test_no_retained_route_accepts_an_identity_parameter(self) -> None:
        """``order_id`` is a resource reference; ``user_id`` would be a caller asserting who it is.

        Asked of the live routes AND of :data:`RETAINED_PARAMETERS`. The first is what FastAPI will
        read; the second stops a later edit from legitimising an identity parameter by adding it to
        the frozen table alongside the handler.
        """
        forbidden = {"user_id", "owner_id", "tenant_id", "author_id", "session_id"}

        live: Dict[str, List[str]] = {}
        for (method, path), (name, _limit) in EXISTING_ROUTES.items():
            route = _route_for(method, path)
            declared = {
                field.name
                for field in list(route.dependant.query_params)
                + list(route.dependant.path_params)
            }
            if declared & forbidden:
                live[name] = sorted(declared & forbidden)
        assert live == {}, (
            f"a retained route accepts an identity from the request: {live}; the acting identity "
            f"comes from the authenticated session and from nowhere else (Requirement 21.1)"
        )

        offenders = {
            handler: sorted(set(params) & forbidden)
            for handler, params in RETAINED_PARAMETERS.items()
            if set(params) & forbidden
        }
        assert offenders == {}, offenders

        declared_bodies = set(paper_trading.PaperOrderRequest.model_fields) | set(
            paper_trading.PaperResetRequest.model_fields
        )
        assert declared_bodies & forbidden == set(), (
            f"a retained body declares an identity field: {sorted(declared_bodies & forbidden)} "
            f"(Requirement 21.1)"
        )

    @pytest.mark.parametrize("path,key", RETAINED_COLLECTION_READS)
    def test_another_tenants_rows_are_never_served(self, path: str, key: str) -> None:
        supabase = _both_accounts(second_tenant=True)
        client = _client(supabase)

        body = client.get(path).json()

        symbols = {row["symbol"] for row in body[key]}
        assert OTHER_TENANT_SYMBOL not in symbols, (
            f"{path} served another tenant's row (Requirement 21.4)"
        )
        assert symbols == {DEFAULT_SYMBOL}

    @pytest.mark.parametrize(
        "path,table",
        [
            ("/api/paper/positions", repo.POSITIONS_TABLE),
            ("/api/paper/orders", repo.ORDERS_TABLE),
            ("/api/paper/trades", repo.FILLS_TABLE),
        ],
    )
    def test_the_caller_is_a_predicate_on_the_statement(self, path: str, table: str) -> None:
        """Requirement 21.5: scoped in the query, never filtered after retrieval."""
        supabase = _both_accounts(second_tenant=True)
        client = _client(supabase)

        client.get(path)

        read = supabase.statements_on(table, "select")[0]
        assert read.filter_value("user_id") == USER, (
            f"{path} reads {table} without the caller as a predicate; its filters are "
            f"{read.filters}"
        )

    def test_another_tenants_account_is_not_the_one_reported(self) -> None:
        supabase = _both_accounts(second_tenant=True)
        client = _client(supabase)

        account = client.get("/api/paper/account").json()

        assert account["account_id"] == _default_account_id(supabase, USER)
        assert account["account_id"] != _default_account_id(supabase, OTHER_USER)


# ══════════════════════════════════════════════════════════════════════════
#  6. THIS FILE'S OWN DISCIPLINE
# ══════════════════════════════════════════════════════════════════════════


def test_this_module_never_calls_asyncio_run() -> None:
    """The paper suite has ONE event loop, and it is ``_run_coroutine``'s.

    A loop per call exhausted this machine's ephemeral port range and hung the paper suite for
    past 700 seconds, which is why every paper test borrows
    ``tests/test_paper_order_lifecycle_writes._run_coroutine``. Asserted rather than remembered,
    because the failure it prevents looks like a hang rather than like a test failure.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    occurrences = [
        line for line in source.splitlines() if "asyncio.run(" in line and "never" not in line
    ]
    assert occurrences == [], occurrences


def test_this_module_does_not_touch_the_frozen_shape_baseline() -> None:
    """The pre-change capture is read in one place, and this is not it.

    ``tests/regression/baseline/paper_api_shape.json`` records what the retained endpoints answered
    before task 23 repointed their storage, and it cannot be re-taken. A module that imported the
    capture helpers could re-record it; a module that imported ``compare`` would be a second
    definition of "compatible" beside the regression gate's. This file therefore imports neither,
    and asserts that it does not - the alternative is remembering.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "tests.regression" in line
    ]
    assert imports == [], (
        f"this module imports from tests.regression: {imports}. The frozen shape baseline is read "
        f"and compared in tests/test_paper_api_shape_compatibility.py and nowhere else"
    )
