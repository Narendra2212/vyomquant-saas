"""
tests/test_paper_api_shape_compatibility.py - the six existing ``/api/paper/*`` endpoints did
not move (task 23.4).

Spec: marketplace-subscriptions-paper-trading task 23.4. Requirements 17.2, 17.12, 25.7, 28.3.

WHAT THIS FILE HOLDS
--------------------
Task 23.2 replaced the storage behind ``GET /api/paper/account``, ``/positions``, ``/orders``,
``/trades``, ``/summary`` and ``POST /api/paper/account/reset``, and task 23.3 added five fields
to their bodies. Three claims follow from that, and each one is asserted here rather than
argued:

1. **Every frozen key is still present, with the same type, and the only difference is task
   23.3's five additive fields.** The frozen record is
   ``tests/regression/baseline/paper_api_shape.json``, captured from the unmodified service in
   task 1.1 - it cannot be re-taken now, so it is read, never written. The comparison uses
   ``tests/regression/capture_baseline.response_shape`` and
   ``tests/regression/test_baseline_unchanged.compare``, the same two functions the regression
   gate uses, so "compatible" means exactly what it means there and a second, laxer definition
   of compatibility cannot creep in beside it.

2. **``?status=OPEN`` still means what it meant.** ``paper_orders`` stores the six canonical
   Paper_Order_State values, and the retained filter reads ``legacy_status``. An account holding
   one ``ACCEPTED`` and one ``PARTIALLY_FILLED`` order must answer with **both**, because both
   are live on the book and both were ``OPEN`` before the repoint (Requirement 17.12). The
   orders are put into those states through the repository, so the assertion is about the
   persisted rows and not about a service method's return value.

3. **With ``paper_accounts`` absent, all six refuse.** 503 ``PAPER_PERSISTENCE_UNAVAILABLE``
   naming ``009_paper_trading.sql`` in ``details.migration`` - and **no** body carries a balance,
   a position, an order or a trade. That second half is the point: a memory-derived figure served
   after this change would be the fabricated figure Requirement 28.3 forbids, and Requirement
   17.2's survive-a-restart guarantee would read as true while being false. The same refusal is
   asserted for the case where there is no Persistence_Layer handle at all, because "no handle"
   and "no table" must not differ in whether a balance is invented.

THE PERSISTENCE_LAYER DOUBLE IS THE ONE FROM ``tests/test_paper_repository.py``
------------------------------------------------------------------------------
``FakeSupabase`` there already enforces the five unique indexes 009 declares -
``uq_paper_account_default``, ``uq_paper_account_session``, ``uq_paper_order_idem`` and its
013 companion, ``uq_paper_fill_event`` and ``uq_paper_position_open`` - and models a missing
relation, a driver failure and an error envelope. A second double would be a second set of
assumptions about the database, and the two would drift.

WHY ``_run_coroutine`` AND NOT ``asyncio.run``
---------------------------------------------
``place_order`` and ``cancel_order`` are ``async``. ``asyncio.run`` closes the loop it created
and leaves the thread with **no** current event loop, which breaks any later test in the same
process that expects one. The helper below restores what it found, the same way
``tests/test_settlement_service.py`` and ``tests/test_marketplace_pipeline.py`` do it.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.paper_order_state import PaperOrderState
from backend_app.backend.paper_trading_service import (
    PaperOrderStatus,
    PaperTradingService,
)
from tests.regression.capture_baseline import read_baseline, response_shape
from tests.regression.test_baseline_unchanged import ADDITIVE_FIELDS, compare
from tests.test_paper_repository import FakeSupabase

USER = "00000000-0000-4000-8000-0000000023d4"
CAPITAL = 100_000.0

#: The six endpoints, in the order the frozen capture visited them. The order matters: the reset
#: is last, because it cancels the resting order and closes the position, and the frozen element
#: shapes of ``/positions`` and ``/orders`` were recorded before that happened.
PAPER_ENDPOINTS = [
    ("GET", "/api/paper/account"),
    ("GET", "/api/paper/positions"),
    ("GET", "/api/paper/orders"),
    ("GET", "/api/paper/trades"),
    ("GET", "/api/paper/summary"),
    ("POST", "/api/paper/account/reset"),
]

#: What a body must never contain when persistence refused. Not an exhaustive list of keys - it
#: is the list of things that would have been *invented*: a balance, an equity figure, a position
#: list, an order list, a fill list.
FABRICATED_IF_PRESENT = (
    "available_balance",
    "locked_balance",
    "total_equity",
    "realized_pnl",
    "unrealized_pnl",
    "account",
    "positions",
    "orders",
    "trades",
)


# ══════════════════════════════════════════════════════════════════════════
# THE LOOP RUNNER
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**."""
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _fresh_probe() -> Any:
    """Forget the cached migration verdict around every test.

    The probe caches a positive verdict for the life of the process, so a test that ran against
    a working double would otherwise decide the verdict for the test that needs the relation to
    be absent.
    """
    repo.reset_persistence_probe()
    yield
    repo.reset_persistence_probe()


@pytest.fixture
def service() -> PaperTradingService:
    """A service bound to a fresh in-memory Persistence_Layer holding the five indexes."""
    instance = PaperTradingService(
        default_capital=CAPITAL, default_fee_rate=0.001, default_slippage=0.0005
    )
    instance.bind_persistence(FakeSupabase())
    return instance


def _client(service_instance: PaperTradingService, user_id: str = USER) -> Any:
    """A ``TestClient`` authenticated as ``user_id``, with the paper singleton bound.

    The router reaches the service through ``get_paper_trading_service()``, so the singleton is
    the instance the endpoints will use; it is bound to the same double the test seeded and
    unbound again by :func:`_release`.
    """
    from fastapi.testclient import TestClient

    from backend_app.backend import paper_trading_service as module
    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    module._paper_service_instance = service_instance
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "sub": user_id,
        "email": "paper-shape@vyomquant.io",
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


def _seed_baseline_scenario(service_instance: PaperTradingService) -> None:
    """The exact scenario the frozen capture recorded, so the element shapes are comparable.

    An open long, a partial close - which realises PnL and leaves a position behind - and one
    resting limit order. An empty list freezes no element shape at all, and the element shape is
    the part a storage change is most likely to disturb.
    """
    service_instance.reset_account(USER, capital=CAPITAL)
    _run_coroutine(
        service_instance.place_order(
            user_id=USER,
            symbol="BTC-USDT",
            side="buy",
            order_type="market",
            quantity=0.5,
            price=60_000.0,
        )
    )
    _run_coroutine(
        service_instance.place_order(
            user_id=USER,
            symbol="BTC-USDT",
            side="sell",
            order_type="market",
            quantity=0.2,
            price=61_000.0,
        )
    )
    _run_coroutine(
        service_instance.place_order(
            user_id=USER,
            symbol="ETH-USDT",
            side="buy",
            order_type="limit",
            quantity=1.0,
            price=3_000.0,
        )
    )


def _captured(client: Any) -> Dict[str, Dict[str, Any]]:
    """The six endpoints' shapes, recorded exactly as ``capture_paper_api_shape`` records them."""
    captured: Dict[str, Dict[str, Any]] = {}
    for method, path in PAPER_ENDPOINTS:
        response = (
            client.get(path)
            if method == "GET"
            else client.post(path, json={"capital": CAPITAL})
        )
        body = response.json()
        captured[f"{method} {path}"] = {
            "status_code": response.status_code,
            **response_shape(body),
            "top_level_keys": sorted(body) if isinstance(body, dict) else "non-object",
        }
    return captured


# ══════════════════════════════════════════════════════════════════════════
# 1. THE FROZEN SHAPE OF EACH OF THE SIX (Requirements 17.12, 25.7)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("endpoint", [f"{m} {p}" for m, p in PAPER_ENDPOINTS])
def test_each_endpoint_still_answers_its_frozen_shape(
    endpoint: str, service: PaperTradingService
) -> None:
    """One test per endpoint, so a difference names the endpoint in the test id itself.

    Every key the baseline records is still present and still has the recorded type; the only
    permitted difference is an added key whose leaf name is one of task 23.3's five.
    """
    frozen = read_baseline("paper_api_shape")["endpoints"][endpoint]
    client = _client(service)
    try:
        _seed_baseline_scenario(service)
        captured = _captured(client)[endpoint]
    finally:
        _release()

    differences = compare(frozen, captured)
    assert differences == [], (
        f"{endpoint} no longer answers the shape the pre-change baseline recorded. Only an "
        f"added {sorted(ADDITIVE_FIELDS)} field is permitted; a removed, renamed, retyped or "
        f"re-meaninged key is a regression (Requirements 17.12, 25.7).\n"
        + "\n".join(differences)
    )


def test_the_only_added_keys_are_the_five_task_23_3_declares(
    service: PaperTradingService,
) -> None:
    """The additive claim, stated the other way round: nothing else appeared.

    ``compare`` permits an addition whose leaf name is additive, so on its own it would also
    pass if a body had gained *nothing*. This asserts the five are actually there - the account
    body carries all five, and every envelope carries the three that are not price-derived - and
    that the difference between the frozen key set and the current one contains nothing else.
    """
    frozen = read_baseline("paper_api_shape")["endpoints"]
    client = _client(service)
    try:
        _seed_baseline_scenario(service)
        captured = _captured(client)
    finally:
        _release()

    unexpected: List[str] = []
    for endpoint, recorded in frozen.items():
        added = set(captured[endpoint]["keys"]) - set(recorded["keys"])
        leaves = {path.rsplit(".", 1)[-1].replace("[]", "") for path in added}
        unexpected.extend(
            f"{endpoint}: {leaf}" for leaf in sorted(leaves - set(ADDITIVE_FIELDS))
        )
    assert unexpected == [], (
        "these keys were added to an existing paper response and are not one of task 23.3's "
        f"five additive fields {sorted(ADDITIVE_FIELDS)}: {unexpected}"
    )

    account_keys = set(captured["GET /api/paper/account"]["keys"])
    assert ADDITIVE_FIELDS <= account_keys, (
        "the account body must carry all five additive fields - it reports figures derived from "
        f"a market price, so ``stale`` and ``last_price_at`` belong on it. Missing: "
        f"{sorted(ADDITIVE_FIELDS - account_keys)}"
    )
    for endpoint in ("GET /api/paper/positions", "GET /api/paper/orders"):
        keys = set(captured[endpoint]["keys"])
        assert {"execution_environment", "is_simulated", "session_id"} <= keys, (
            f"{endpoint} must carry the environment label, the simulated marker and the null "
            f"session id (Requirements 13.6, 28.1). Present: {sorted(keys)}"
        )


def test_every_paper_body_says_it_is_simulated(service: PaperTradingService) -> None:
    """Requirements 13.6 and 28.1, read as values rather than as key names."""
    client = _client(service)
    try:
        _seed_baseline_scenario(service)
        account = client.get("/api/paper/account").json()
        positions = client.get("/api/paper/positions").json()
        summary = client.get("/api/paper/summary").json()
    finally:
        _release()

    for body in (account, positions, summary):
        assert body["execution_environment"] == "PAPER"
        assert body["is_simulated"] is True
        assert body["session_id"] is None

    assert account["stale"] is True, (
        "an open position valued at the price recorded on its row - not at a price supplied "
        "with this call - is stale (Requirement 18.15)"
    )
    assert account["last_price_at"] is not None
    assert positions["positions"], "the scenario leaves one open position"
    for position in positions["positions"]:
        assert position["execution_environment"] == "PAPER"
        assert position["is_simulated"] is True
        assert "stale" in position and "last_price_at" in position


def test_the_reported_figures_survive_a_new_service_instance(
    service: PaperTradingService,
) -> None:
    """Requirement 17.2, stated as the thing the old implementation could not do.

    The figures are read back through a **different** ``PaperTradingService`` object bound to
    the same Persistence_Layer, with no shared process state between the two. Under the in-memory
    implementation this returned a pristine account.
    """
    _seed_baseline_scenario(service)
    before = service.get_or_create_account(USER)
    positions_before = service.get_positions(USER)

    successor = PaperTradingService(
        default_capital=CAPITAL, default_fee_rate=0.001, default_slippage=0.0005
    )
    successor.bind_persistence(service._supabase)

    after = successor.get_or_create_account(USER)
    assert Decimal(after["available_balance"]) == Decimal(before["available_balance"])
    assert Decimal(after["total_equity"]) == Decimal(before["total_equity"])
    assert Decimal(after["realized_pnl"]) == Decimal(before["realized_pnl"])
    assert [p["symbol"] for p in successor.get_positions(USER)] == [
        p["symbol"] for p in positions_before
    ]
    assert successor.verify_accounting_invariants(USER) is True


# ══════════════════════════════════════════════════════════════════════════
# 2. ``?status=OPEN`` STILL MEANS "LIVE ON THE BOOK" (Requirement 17.12)
# ══════════════════════════════════════════════════════════════════════════


def _persist_order(
    client: FakeSupabase,
    account_id: str,
    *,
    state: PaperOrderState,
    symbol: str,
    filled_quantity: str = "0",
) -> Dict[str, Any]:
    """Record one default-account order and move it to ``state`` through the repository.

    Written at the storage layer on purpose: the claim under test is about what
    ``legacy_status`` the rows carry and what the endpoint's predicate then returns, so going
    through the service's fill path would test the fill path instead.
    """
    created = repo.insert_order(
        client,
        account_id=account_id,
        user_id=USER,
        symbol=symbol,
        side="buy",
        order_type="limit",
        quantity="1",
        limit_price="100",
        reference_price="100",
        fingerprint=f"fingerprint-{symbol}-{state.value}",
    )
    if state is PaperOrderState.CREATED:
        return created
    accepted = repo.update_order(
        client,
        user_id=USER,
        order_id=created["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )
    if state is PaperOrderState.ACCEPTED:
        return accepted
    return repo.update_order(
        client,
        user_id=USER,
        order_id=created["id"],
        order_state=state,
        expected_state=PaperOrderState.ACCEPTED,
        filled_quantity=filled_quantity,
    )


def test_status_open_returns_accepted_and_partially_filled_orders(
    service: PaperTradingService,
) -> None:
    """The retained filter, on the exact account task 23.4 names.

    One ``ACCEPTED`` order and one ``PARTIALLY_FILLED`` order: both are ``OPEN``, so both are
    returned - and the other four states are not, so ``OPEN`` did not quietly widen either.
    """
    account = service.get_or_create_account(USER)
    double = service._supabase
    account_id = account["account_id"]

    _persist_order(double, account_id, state=PaperOrderState.ACCEPTED, symbol="BTC-USDT")
    _persist_order(
        double,
        account_id,
        state=PaperOrderState.PARTIALLY_FILLED,
        symbol="ETH-USDT",
        filled_quantity="0.4",
    )
    _persist_order(double, account_id, state=PaperOrderState.CREATED, symbol="SOL-USDT")
    _persist_order(double, account_id, state=PaperOrderState.FILLED, symbol="XRP-USDT")
    _persist_order(double, account_id, state=PaperOrderState.CANCELLED, symbol="ADA-USDT")

    client = _client(service)
    try:
        body = client.get("/api/paper/orders?status=OPEN").json()
        unfiltered = client.get("/api/paper/orders").json()
    finally:
        _release()

    assert body["count"] == 2, body
    assert {order["symbol"] for order in body["orders"]} == {"BTC-USDT", "ETH-USDT"}
    assert {order["status"] for order in body["orders"]} == {PaperOrderStatus.OPEN.value}
    assert unfiltered["count"] == 5, "the unfiltered read returns every order, as it always did"
    assert {order["status"] for order in unfiltered["orders"]} == {
        PaperOrderStatus.NEW.value,
        PaperOrderStatus.OPEN.value,
        PaperOrderStatus.FILLED.value,
        PaperOrderStatus.CANCELLED.value,
    }


def test_a_concurrent_duplicate_intent_answers_with_the_order_that_won(
    service: PaperTradingService,
) -> None:
    """Requirement 16.8, in the case the probe cannot cover.

    ``self._idempotency_cache`` is gone; the key is arbitrated by
    ``uq_paper_order_idem_default``. Two first requests carrying the same key can both read no
    order and both insert, and the index refuses one of them - which is the guarantee working.
    The loser must answer with the order that won rather than raising, and above all rather than
    placing a second order.
    """
    account = service.get_or_create_account(USER)
    double = service._supabase
    key = "idem-concurrent-0001"
    state: Dict[str, Any] = {"done": False, "row": None}

    def _a_concurrent_first_request(client: Any, query: Any) -> None:
        """Record the same key from another request, in the instant before this insert lands."""
        if query.table_name != repo.ORDERS_TABLE or state["done"]:
            return
        state["done"] = True  # set first: the insert below re-enters this hook
        state["row"] = repo.insert_order(
            client,
            account_id=account["account_id"],
            user_id=USER,
            symbol="BTC-USDT",
            side="buy",
            order_type="market",
            quantity="0.5",
            reference_price="60000",
            fingerprint="the-winner",
            idempotency_key=key,
        )

    double.before_insert = _a_concurrent_first_request
    try:
        answered = _run_coroutine(
            service.place_order(
                user_id=USER,
                symbol="BTC-USDT",
                side="buy",
                order_type="market",
                quantity=0.5,
                price=60_000.0,
                idempotency_key=key,
            )
        )
    finally:
        double.before_insert = None

    assert answered["order_id"] == str(state["row"]["id"]), (
        "the request that lost the index race must answer with the recorded order"
    )
    under_key = [
        row for row in double.orders if row.get("idempotency_key") == key
    ]
    assert len(under_key) == 1, (
        f"exactly one order may exist under one idempotency key; found {len(under_key)}"
    )


def test_a_filled_order_carries_fee_and_execution_id_and_a_resting_one_does_not(
    service: PaperTradingService,
) -> None:
    """The union the baseline recorded, asserted as a union.

    ``fee`` and ``execution_id`` belong to an order that has a fill. Adding them to a resting
    order would add two keys to a variant that never carried them, which is a shape change even
    though nothing was removed.
    """
    client = _client(service)
    try:
        _seed_baseline_scenario(service)
        orders = client.get("/api/paper/orders").json()["orders"]
    finally:
        _release()

    filled = [o for o in orders if o["status"] == PaperOrderStatus.FILLED.value]
    resting = [o for o in orders if o["status"] == PaperOrderStatus.OPEN.value]
    assert filled and resting, orders
    for order in filled:
        assert "fee" in order and "execution_id" in order
    for order in resting:
        assert "fee" not in order and "execution_id" not in order


# ══════════════════════════════════════════════════════════════════════════
# 3. AN ABSENT ``paper_accounts`` IS AN OUTAGE, NOT A MEMORY BALANCE
#    (Requirements 17.2, 28.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("method,path", PAPER_ENDPOINTS)
def test_each_endpoint_refuses_503_when_009_is_unapplied(method: str, path: str) -> None:
    """One test per endpoint: 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` naming the migration."""
    absent = PaperTradingService(default_capital=CAPITAL)
    absent.bind_persistence(FakeSupabase(missing_tables=True))
    client = _client(absent)
    try:
        response = (
            client.get(path)
            if method == "GET"
            else client.post(path, json={"capital": CAPITAL})
        )
    finally:
        _release()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"]["code"] == "PAPER_PERSISTENCE_UNAVAILABLE", body
    assert body["error"]["details"]["migration"] == "009_paper_trading.sql", (
        "the refusal must name the file an operator applies, as its bare name - a repository "
        "path is replaced by the redaction placeholder and would name nothing"
    )
    for key in FABRICATED_IF_PRESENT:
        assert key not in body, (
            f"the refusal body carries {key!r}. A balance, a position list or a fill list "
            "served while persistence is unavailable is the fabricated figure Requirement 28.3 "
            "forbids"
        )


@pytest.mark.parametrize("method,path", PAPER_ENDPOINTS)
def test_each_endpoint_refuses_503_when_there_is_no_persistence_handle(
    method: str, path: str
) -> None:
    """No handle must not differ from no table in whether a figure is invented.

    Nothing is bound and the environment has no Supabase credentials, so the service can obtain
    no handle at all. The old implementation answered 200 from its dictionaries in exactly this
    situation.
    """
    unbound = PaperTradingService(default_capital=CAPITAL)
    client = _client(unbound)
    try:
        response = (
            client.get(path)
            if method == "GET"
            else client.post(path, json={"capital": CAPITAL})
        )
    finally:
        _release()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"]["code"] == "PAPER_PERSISTENCE_UNAVAILABLE", body
    for key in FABRICATED_IF_PRESENT:
        assert key not in body


def test_a_read_that_did_not_complete_is_not_an_empty_answer() -> None:
    """A driver failure answers 503 ``PAPER_READ_FAILED`` - a different code, deliberately.

    ``PAPER_PERSISTENCE_UNAVAILABLE`` means the migration is unapplied. Answering it for a
    transient failure would name a file that is already applied and send an operator to the
    wrong place; answering 200 with an empty list would report a flat book the trader does not
    have.
    """
    broken = PaperTradingService(default_capital=CAPITAL)
    broken.bind_persistence(
        FakeSupabase(raise_on={("select", repo.POSITIONS_TABLE)})
    )
    client = _client(broken)
    try:
        response = client.get("/api/paper/positions")
    finally:
        _release()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"]["code"] == "PAPER_READ_FAILED", body
    assert "positions" not in body
    assert "migration" not in (body["error"].get("details") or {}), (
        "a transient read failure must not name a migration: there is no file to apply"
    )
