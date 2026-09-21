"""
tests/test_risk_utilisation_persisted_account.py - Requirement 25.5 for ``routers/risk.py``.

marketplace-subscriptions-paper-trading task 23.5. ``GET /api/risk/status`` and
``GET /api/risk/margin-health`` used to read the paper account out of process memory. They now
read the persisted Paper_Account, and Requirement 25.5 says that repoint must happen "without
changing its reported semantics" - so this module asserts the reported figures, not the
plumbing.

WHAT IS ASSERTED, AND WHY EACH PART IS HERE
-------------------------------------------
1. **The frozen figures.** ``tests/regression/baseline/risk_utilisation.json`` was captured from
   the unmodified system and pins numbers rather than types, because "unchanged semantics" is a
   claim about numbers: ``margin_ratio`` 9.76, ``free_margin`` 68.33, ``risk_score`` 14,
   ``status`` ``WARNING``, loss utilisation 60.0, position utilisation 20.0. The account is
   seeded through the **repository** here, which is the whole point - the same numbers have to
   come out of rows that they used to come out of dictionaries. Requirements 25.5, 25.7.

   ONE FROZEN FIGURE HAS DELIBERATELY BEEN RE-FROZEN: ``drawdown_pct``.
       The capture pinned ``type_map.drawdown_pct = "float"`` and ``values.drawdown_pct = 0.0``.
       vyomquant-ui-redesign BC-1 (that spec's task 12.1) replaced the hardcoded literal in
       ``routers/risk.py::get_risk_status`` with an honest ``null``: no equity series reaches
       that handler, and a literal zero claims "this account is sitting at its peak" about
       something nothing measured. That one change is explicitly carved out of that spec's
       do-not-touch list - vyomquant-ui-redesign ``design.md`` §17.1 reads "No change to
       ``backend_app/routers/risk.py``'s limits, kill switch, or circuit breakers, **beyond
       BC-1's honest null in place of a hardcoded 0.0 in one read projection**" - so the
       baseline was updated to ``"null"`` / ``null`` rather than the change being reverted or
       this assertion weakened. ``"null"`` is the spelling ``capture_baseline.shape_of`` emits
       for ``None``, so the type map stays in the capture's own vocabulary.

       **Nothing else in ``risk_utilisation.json`` moved.** Requirement 25.5's semantics claim
       is about the arithmetic of the utilisation figures - ``margin_ratio``, ``free_margin``,
       ``risk_score``, the two utilisation percentages and which rung of the risk ladder they
       land on - and BC-1 touched none of them. ``drawdown_pct`` was never computed from the
       paper account at all, which is exactly why it could not honestly be a number.

2. **A ``size = 0`` row must not be counted.** A fully closed position persists at ``size = 0``
   rather than being deleted (Requirement 18.5), which is the one place the storage change could
   otherwise move a reported figure: the in-memory dictionary expressed "closed" structurally by
   removing the entry, so ``len(get_positions(uid))`` could never see one. The filter therefore
   lives at the reporting boundary in ``risk.py`` as well, and this test is what holds it there.
   The row is seeded with ``closed_at IS NULL`` deliberately - a crash between the fill insert
   and the close marker leaves exactly that row, and the statement's ``closed_at IS NULL``
   predicate would return it.

3. **A persistence refusal is a 503, never a default balance.** ``get_or_create_account``,
   ``get_positions`` and ``get_performance_summary`` raise ``PaperError`` - 503
   ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied, 503 ``PAPER_READ_FAILED`` when a
   statement did not complete. Both risk endpoints must let those reach the client with their
   catalogue code. Answering 200 with the ``100000.0`` default that the ``.get`` calls carry
   would be precisely the fabricated figure Requirement 28.3 forbids: a trader whose account is
   unreadable would be shown a healthy margin.

WHERE THE STATE COMES FROM
--------------------------
``tests/paper_seed.py`` writes it through ``backend/paper/paper_repository.py`` onto
``tests/test_paper_repository.FakeSupabase`` - the single sanctioned Persistence_Layer double in
this repository, which enforces the five unique indexes ``009_paper_trading.sql`` declares. No
second double, and no assignment to a private attribute of the service.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterator, Tuple
from uuid import uuid4

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper_trading_service import PaperTradingService
from tests.paper_seed import seed_account, seed_position
from tests.regression.capture_baseline import read_baseline, response_shape
from tests.test_paper_repository import FakeSupabase

#: The capital the baseline capture opened the account with.
CAPITAL = 100_000.0

#: The instant the seeded positions were opened and last priced at. Fixed, so the seeded rows are
#: reproducible; a clock read is not.
SEED_INSTANT = "2024-05-01T12:00:00+00:00"

#: The frozen pre-change capture. Read once - it is the contract, not a fixture.
BASELINE: Dict[str, Any] = read_baseline("risk_utilisation")

#: The two positions the capture seeded. ``side`` is the response spelling; ``paper_positions``
#: stores ``LONG`` / ``SHORT`` (``chk_paper_position_side``) and ``seed_position`` maps it.
SEEDED_POSITIONS: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    ("BTC-USDT", "long", "0.25", "60000.00", "61000.00", "250.00"),
    ("ETH-USDT", "short", "2.0", "3500.00", "3400.00", "200.00"),
)

#: Keys whose presence in a refusal body would mean a figure was served in place of an answer.
FABRICATED_IF_PRESENT = (
    "margin_ratio",
    "free_margin",
    "risk_score",
    "daily_loss",
    "positions",
)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _fresh_probe() -> Iterator[None]:
    """Forget the cached migration verdict around every test.

    The probe caches a positive verdict for the life of the process, so a test that ran against a
    working double would otherwise decide the verdict for the test that needs the relation absent.
    """
    repo.reset_persistence_probe()
    yield
    repo.reset_persistence_probe()


def _service(supabase: Any = None) -> PaperTradingService:
    """A paper service bound to a fresh in-memory Persistence_Layer."""
    instance = PaperTradingService(
        default_capital=CAPITAL, default_fee_rate=0.001, default_slippage=0.0005
    )
    instance.bind_persistence(FakeSupabase() if supabase is None else supabase)
    return instance


def _client(service: PaperTradingService, user_id: str) -> Any:
    """A ``TestClient`` authenticated as ``user_id``, with the paper singleton bound.

    ``routers/risk.py`` reaches the service through ``get_paper_trading_service()``, so the
    singleton is the instance the handlers will use.
    """
    from fastapi.testclient import TestClient

    from backend_app.backend import paper_trading_service as module
    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app

    module._paper_service_instance = service
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "sub": user_id,
        "email": "risk-utilisation@vyomquant.io",
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


def _seed_known_account(service: PaperTradingService, user_id: str) -> None:
    """The capture's account, written through the repository.

    ``realized_pnl = -300`` against the default ``max_daily_loss`` of 500 puts loss utilisation
    at exactly 60 % - the WARNING boundary - so the threshold ladder is pinned and not merely the
    ratio. Two open positions against the default ``max_positions`` of 10 put position
    utilisation at 20 %. The locked balance makes ``margin_ratio`` and ``free_margin``
    non-trivial.

    ``total_equity`` is ``70000 + 10000 + (0.25 * 61000) + (2 * (2 * 3500 - 3400))`` = 102450.00,
    the identity Requirement 18.3 holds to zero tolerance, so the seeded row is itself consistent.
    """
    service.reset_account(user_id, capital=CAPITAL)
    for symbol, side, size, entry, current, unrealized in SEEDED_POSITIONS:
        seed_position(
            service,
            user_id,
            symbol=symbol,
            side=side,
            size=size,
            entry_price=entry,
            current_price=current,
            unrealized_pnl=unrealized,
            opened_at=SEED_INSTANT,
            price_at=SEED_INSTANT,
        )
    seed_account(
        service,
        user_id,
        available_balance="70000.00",
        locked_balance="10000.00",
        realized_pnl="-300.00",
        total_equity="102450.00",
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE FROZEN FIGURES STILL HOLD, READ FROM ROWS  (Requirements 25.5, 25.7)
# ══════════════════════════════════════════════════════════════════════════


def test_seeded_account_matches_the_baselines_known_account() -> None:
    """The seeded rows are the capture's account, so the assertions below are like-for-like.

    Compared as ``Decimal``, not as ``float``: the figures are money and the baseline records
    them as decimal strings (Requirements 8.13, 16.12, 18.1).
    """
    user_id = str(uuid4())
    service = _service()
    _seed_known_account(service, user_id)

    account = service.get_or_create_account(user_id)
    known = BASELINE["known_account"]

    for field in (
        "available_balance",
        "locked_balance",
        "total_equity",
        "realized_pnl",
        "unrealized_pnl",
        "initial_capital",
    ):
        assert Decimal(str(account[field])) == Decimal(str(known[field])), (
            f"{field}: seeded {account[field]!r}, baseline {known[field]!r}"
        )

    assert len(service.get_positions(user_id)) == known["open_position_count"]


def test_risk_status_reports_the_frozen_utilisation_figures() -> None:
    """``GET /api/risk/status`` against the frozen capture. Requirement 25.5."""
    user_id = str(uuid4())
    service = _service()
    _seed_known_account(service, user_id)

    client = _client(service, user_id)
    try:
        response = client.get("/api/risk/status")
    finally:
        _release()

    frozen = BASELINE["status_endpoint"]
    assert response.status_code == frozen["status_code"], response.text
    body = response.json()

    shape = response_shape(body)
    assert shape["keys"] == frozen["keys"], (
        "the response key set changed; Requirement 25.5 permits no removal, rename or addition "
        "here - only where the numbers come from was allowed to change"
    )
    # ``drawdown_pct`` is ``"null"`` / ``null`` in the baseline rather than ``"float"`` / ``0.0``
    # as captured. That is vyomquant-ui-redesign BC-1, sanctioned in that spec's design.md §17.1
    # ("...beyond BC-1's honest null in place of a hardcoded 0.0 in one read projection"). See
    # this module's docstring, item 1. Every other frozen figure below is the original capture.
    assert shape["type_map"] == frozen["type_map"]

    for key, expected in frozen["values"].items():
        assert body[key] == expected, f"{key}: baseline {expected!r}, now {body[key]!r}"


def test_margin_health_reports_the_frozen_margin_figures() -> None:
    """``GET /api/risk/margin-health`` against the frozen capture. Requirement 25.5."""
    user_id = str(uuid4())
    service = _service()
    _seed_known_account(service, user_id)

    client = _client(service, user_id)
    try:
        response = client.get("/api/risk/margin-health")
    finally:
        _release()

    frozen = BASELINE["margin_health_endpoint"]
    assert response.status_code == frozen["status_code"], response.text
    body = response.json()

    shape = response_shape(body)
    assert shape["keys"] == frozen["keys"]
    assert shape["type_map"] == frozen["type_map"], (
        "``risk_score`` is an ``int`` and the two ratios are ``float``s; the exact decimal "
        "arithmetic behind them must not surface as a string or a Decimal on the wire"
    )

    for key, expected in frozen["values"].items():
        assert body[key] == expected, f"{key}: baseline {expected!r}, now {body[key]!r}"


# ══════════════════════════════════════════════════════════════════════════
# 2. A ``size = 0`` ROW IS NOT AN OPEN POSITION  (Requirements 18.5, 25.5)
# ══════════════════════════════════════════════════════════════════════════


def test_position_utilisation_ignores_a_zero_size_row_left_open() -> None:
    """A ``size = 0`` position with ``closed_at IS NULL`` must not be counted.

    The one figure the storage change could otherwise move. A fully closed position persists at
    ``size = 0`` (Requirement 18.5) instead of being deleted, and a process that stopped between
    writing the fill and writing the close marker leaves that row with ``closed_at`` still null -
    so the ``closed_at IS NULL`` predicate in the statement returns it. Counting it would report
    an open position the trader does not have, and at ``max_positions`` it would block an order.
    """
    user_id = str(uuid4())
    service = _service()
    _seed_known_account(service, user_id)
    seed_position(
        service,
        user_id,
        symbol="SOL-USDT",
        side="long",
        size="0",
        entry_price="150.00",
        current_price="150.00",
        unrealized_pnl="0",
        opened_at=SEED_INSTANT,
        price_at=SEED_INSTANT,
    )

    # The premise, asserted rather than assumed: the statement's ``closed_at IS NULL`` predicate
    # really does return the zero-size row, so the count below is the result of a filter and not
    # of an empty read. Both boundaries then exclude it - ``get_positions``' ``is_open``, and
    # ``risk.py``'s own ``size > 0`` - and this test holds the second one in place.
    persisted = repo.get_positions(service._supabase, user_id)
    assert sorted(row["symbol"] for row in persisted) == [
        "BTC-USDT",
        "ETH-USDT",
        "SOL-USDT",
    ], persisted
    assert [row["size"] for row in persisted if row["symbol"] == "SOL-USDT"] == ["0"]
    assert [row["closed_at"] for row in persisted if row["symbol"] == "SOL-USDT"] == [None]

    client = _client(service, user_id)
    try:
        response = client.get("/api/risk/status")
    finally:
        _release()

    assert response.status_code == 200, response.text
    body = response.json()
    frozen = BASELINE["status_endpoint"]["values"]["positions"]
    assert body["positions"]["current"] == frozen["current"], (
        "a zero-size row was counted as an open position; the persisted representation of "
        "'closed' must report the same count the deleted dictionary entry reported"
    )
    assert body["positions"]["utilization_pct"] == frozen["utilization_pct"]
    assert body["status"] == BASELINE["status_endpoint"]["values"]["status"]


# ══════════════════════════════════════════════════════════════════════════
# 3. A REFUSAL IS A 503, NOT A DEFAULT BALANCE  (Requirements 17.2, 28.3)
# ══════════════════════════════════════════════════════════════════════════


RISK_READ_ENDPOINTS = ("/api/risk/status", "/api/risk/margin-health")


@pytest.mark.parametrize("path", RISK_READ_ENDPOINTS)
def test_each_risk_endpoint_refuses_503_when_009_is_unapplied(path: str) -> None:
    """With ``paper_accounts`` absent, both endpoints answer 503 naming the migration."""
    user_id = str(uuid4())
    service = _service(FakeSupabase(missing_tables=True))

    client = _client(service, user_id)
    try:
        response = client.get(path)
    finally:
        _release()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"]["code"] == "PAPER_PERSISTENCE_UNAVAILABLE", body
    assert body["error"]["details"]["migration"] == "009_paper_trading.sql", body
    for key in FABRICATED_IF_PRESENT:
        assert key not in body, (
            f"{path} answered a refusal with {key!r}; an unreadable account must not be "
            "reported as a healthy one (Requirement 28.3)"
        )


@pytest.mark.parametrize(
    "path,table",
    [
        ("/api/risk/status", "paper_positions"),
        ("/api/risk/margin-health", "paper_accounts"),
    ],
)
def test_each_risk_endpoint_refuses_503_when_a_read_did_not_complete(
    path: str, table: str
) -> None:
    """A driver failure answers 503 ``PAPER_READ_FAILED``, and names no migration.

    A different code from the unapplied-migration one on purpose: there is no file to apply, and
    sending an operator to one that is already applied wastes the outage.
    """
    user_id = str(uuid4())
    service = _service(FakeSupabase(raise_on={("select", table)}))

    client = _client(service, user_id)
    try:
        response = client.get(path)
    finally:
        _release()

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error"]["code"] == "PAPER_READ_FAILED", body
    assert "migration" not in (body["error"].get("details") or {}), body
    for key in FABRICATED_IF_PRESENT:
        assert key not in body
