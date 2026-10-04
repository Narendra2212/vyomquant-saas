"""
tests/test_strategy_metrics_tenant_isolation.py

The six strategy-metrics endpoints must refuse a caller who does not own the strategy.

WHAT WAS WRONG
--------------
``MetricsService`` declares every one of its seven read methods as
``(self, user_id, strategy_id, ...)`` and **never uses** ``user_id``. Every query it builds
is keyed on the strategy alone::

    FROM strategy_performance WHERE strategy_id = '{safe_sid}'
    FROM strategy_risk        WHERE strategy_id = '{safe_sid}'
    FROM executions           WHERE strategy_id = '{safe_sid}'

and the six handlers in ``backend_app/routers/strategy_operations.py`` went straight from
``try:`` to ``get_metrics_service()`` to the service call, resolving **no ownership at all**.
So any authenticated caller holding any strategy id was answered that strategy's
performance, equity curve, monthly and daily returns, execution/fill metrics and risk state
— including its drawdown, exposure, position limits and **kill-switch state**.

These endpoints read a telemetry store (QuestDB), not ``strategies``, so — unlike every
other resource read in this router — **no RLS predicate stands behind the missing
handler-level check**. There was nothing else in the stack to catch it.

WHY THIS FILE EXISTS SEPARATELY FROM tests/test_strategy_metrics_endpoints.py
-----------------------------------------------------------------------------
That file pins the *call-site signature* agreement (the earlier defect, where the handlers
passed ``user=user`` to a method declaring ``user_id`` and every request 500'd). It states
in its own docstring that the ownership gap is "deliberately not asserted here". This file
asserts exactly that gap, so the two do not have to be read together to know which
property is covered where.

WHAT IS ASSERTED
----------------
1. A caller who does not own the strategy is answered **404**, identical to the answer a
   non-existent id gets — never 403, because a distinguishable refusal is an existence
   oracle (the convention the rest of this router follows, Requirement 21.4).
2. The metrics service is **never reached** on that path. A refusal that still issues the
   read has already touched the other tenant's data and is one logging change away from
   disclosing it.
3. The owner is unaffected: same 200, same figures.
4. A read that **did not complete** is a 503, not a 404. Reporting "no such strategy"
   because an ownership read broke would tell an owner something false about their own
   strategy — the distinction this codebase draws everywhere else.
5. The gate is present in the source of all six handlers, so a seventh metrics endpoint
   cannot be added without one.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import strategy_operations

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_SOURCE = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

OWNER_ID = "66666666-6666-4666-8666-666666666666"
STRANGER_ID = "55555555-5555-4555-8555-555555555555"
STRATEGY_ID = "77777777-7777-4777-8777-777777777777"

OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "owner@test.vyomquant.io",
    "role": "authenticated",
    "access_token": "owner-token",
}
STRANGER: Dict[str, Any] = {
    "id": STRANGER_ID,
    "email": "stranger@test.vyomquant.io",
    "role": "authenticated",
    "access_token": "stranger-token",
}

#: Deliberately not a round number and not a threshold, so its presence in a body cannot be
#: a coincidence of some default. If this reaches a stranger, it came from the owner's rows.
MARKER = 8675.309


# ---------------------------------------------------------------------------
# The six endpoints
# ---------------------------------------------------------------------------

class Endpoint:
    def __init__(self, suffix: str, handler: str, service_method: str) -> None:
        self.suffix = suffix
        self.handler = handler
        self.service_method = service_method

    @property
    def url(self) -> str:
        return f"/api/strategies/{STRATEGY_ID}/{self.suffix}"

    def __repr__(self) -> str:  # pytest node id
        return self.suffix


ENDPOINTS: Tuple[Endpoint, ...] = (
    Endpoint("performance", "get_strategy_performance", "get_strategy_performance"),
    Endpoint("equity-curve", "get_strategy_equity_curve", "get_equity_curve"),
    Endpoint("monthly-returns", "get_strategy_monthly_returns", "get_monthly_returns"),
    Endpoint("daily-returns", "get_strategy_daily_returns", "get_daily_returns"),
    Endpoint(
        "execution-metrics",
        "get_strategy_execution_metrics",
        "get_execution_metrics",
    ),
    Endpoint("risk-metrics", "get_strategy_risk_metrics", "get_risk_metrics"),
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _DiscloseEverythingMetricsService:
    """A metrics service that answers the owner's figures to whoever asks.

    This is not a caricature — it is what the real ``MetricsService`` does, because its
    queries carry no tenant predicate. Recording every call is the point: assertion 2 is
    that the refusal happens *before* this is reached.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append({"method": method, **kwargs})

    async def get_strategy_performance(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        self._record("get_strategy_performance", user_id=user_id, strategy_id=strategy_id)
        return {"total_pnl": MARKER}

    async def get_equity_curve(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        self._record("get_equity_curve", user_id=user_id, strategy_id=strategy_id)
        return [{"total_equity": MARKER}]

    async def get_monthly_returns(self, user_id: str, strategy_id: str) -> List[Dict]:
        self._record("get_monthly_returns", user_id=user_id, strategy_id=strategy_id)
        return [{"return_pct": MARKER}]

    async def get_daily_returns(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        self._record("get_daily_returns", user_id=user_id, strategy_id=strategy_id)
        return [{"return_pct": MARKER}]

    async def get_execution_metrics(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        self._record("get_execution_metrics", user_id=user_id, strategy_id=strategy_id)
        return {"total_filled": MARKER}

    async def get_risk_metrics(self, user_id: str, strategy_id: str) -> Dict:
        self._record("get_risk_metrics", user_id=user_id, strategy_id=strategy_id)
        return {"current_exposure": MARKER, "kill_switch_active": True}


class _OwnershipStub:
    """The ``StrategyService`` seam the gate resolves ownership through.

    ``owns`` decides the verdict; ``raises`` makes the read fail instead of answering, which
    is assertion 4's case. ``asked`` records the calls so a test can prove the gate ran.
    """

    def __init__(self, *, owns: bool = True, raises: Optional[BaseException] = None) -> None:
        self._owns = owns
        self._raises = raises
        self.asked: List[Tuple[str, str]] = []

    async def owns_strategy(self, user: dict, strategy_id: str) -> bool:
        self.asked.append((user.get("id", ""), strategy_id))
        if self._raises is not None:
            raise self._raises
        return self._owns


def _install(monkeypatch, *, metrics: Any, ownership: Any) -> None:
    """Replace the router's two seams: the metrics service and the ownership resolver."""

    async def _get_metrics_service() -> Any:
        return metrics

    async def _get_strategy_service() -> Any:
        return ownership

    monkeypatch.setattr(
        strategy_operations, "get_metrics_service", _get_metrics_service
    )
    monkeypatch.setattr(
        strategy_operations, "get_strategy_service", _get_strategy_service
    )


@pytest.fixture
def client_as():
    """A ``TestClient`` authenticated as whichever identity the test names."""
    created: List[TestClient] = []

    def _build(identity: Dict[str, Any]) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: dict(identity)
        c = TestClient(app, raise_server_exceptions=False)
        created.append(c)
        return c

    try:
        yield _build
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 1 + 2. A stranger is refused, and the read is never issued
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_a_stranger_is_answered_404_and_no_figure_of_the_owners(
    endpoint: Endpoint, client_as, monkeypatch
):
    """The defect, stated as a test.

    Before the fix this returns 200 carrying ``MARKER`` — the owner's own figure — to a
    caller who owns nothing.
    """
    metrics = _DiscloseEverythingMetricsService()
    ownership = _OwnershipStub(owns=False)
    _install(monkeypatch, metrics=metrics, ownership=ownership)

    response = client_as(STRANGER).get(endpoint.url)

    assert response.status_code == 404, (
        f"GET {endpoint.url} answered {response.status_code} to a caller who does not own "
        f"the strategy. Expected 404, indistinguishable from a non-existent id.\n"
        f"Body: {response.text[:400]!r}"
    )
    assert str(MARKER) not in response.text, (
        f"GET {endpoint.url} disclosed the owner's figure ({MARKER}) to a stranger. "
        f"Body: {response.text[:400]!r}"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_metrics_read_is_never_issued_for_a_stranger(
    endpoint: Endpoint, client_as, monkeypatch
):
    """Assertion 2: refuse *before* touching the telemetry store.

    A gate that issues the read and then discards the answer has already loaded another
    tenant's rows into this process, and is one log line or one error body away from
    disclosing them.
    """
    metrics = _DiscloseEverythingMetricsService()
    ownership = _OwnershipStub(owns=False)
    _install(monkeypatch, metrics=metrics, ownership=ownership)

    client_as(STRANGER).get(endpoint.url)

    assert metrics.calls == [], (
        f"GET {endpoint.url} reached the metrics service for a caller who owns nothing: "
        f"{metrics.calls!r}. The ownership gate must precede the read."
    )
    assert ownership.asked == [(STRANGER_ID, STRATEGY_ID)], (
        f"the ownership gate was asked {ownership.asked!r}; it must be asked exactly once, "
        f"with the authenticated caller's own id and the path's strategy id"
    )


# ---------------------------------------------------------------------------
# 3. The owner is unaffected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_owner_still_gets_their_figures(endpoint: Endpoint, client_as, monkeypatch):
    """The gate must not cost the owner their own data.

    This is the regression half: a fix that refused everybody would pass the tests above.
    """
    metrics = _DiscloseEverythingMetricsService()
    ownership = _OwnershipStub(owns=True)
    _install(monkeypatch, metrics=metrics, ownership=ownership)

    response = client_as(OWNER).get(endpoint.url)

    assert response.status_code == 200, (
        f"GET {endpoint.url} answered {response.status_code} to the strategy's OWNER.\n"
        f"Body: {response.text[:400]!r}"
    )
    assert str(MARKER) in response.text, (
        f"the owner's own figure did not reach the response body. "
        f"Body: {response.text[:400]!r}"
    )
    assert [call["method"] for call in metrics.calls] == [endpoint.service_method], (
        f"the owner's request reached {metrics.calls!r}; expected exactly one "
        f"{endpoint.service_method} call"
    )
    assert metrics.calls[0]["user_id"] == OWNER_ID, (
        "the handler must still pass the authenticated caller's own id as ``user_id``"
    )


# ---------------------------------------------------------------------------
# 4. A failed ownership read is 503, not 404
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_an_unreadable_ownership_check_is_503_not_404(
    endpoint: Endpoint, client_as, monkeypatch
):
    """"Could not find out" is not "you do not own it".

    The convention this codebase applies everywhere else (``MARKETPLACE_READ_FAILED``,
    ``PAPER_READ_FAILED``, the 503 on an unverifiable freeze status): a read that did not
    complete must not be reported as an established absence. A 404 here would tell an owner
    their own strategy does not exist because a database hiccuped.
    """
    metrics = _DiscloseEverythingMetricsService()
    ownership = _OwnershipStub(raises=RuntimeError("ownership read did not complete"))
    _install(monkeypatch, metrics=metrics, ownership=ownership)

    response = client_as(OWNER).get(endpoint.url)

    assert response.status_code == 503, (
        f"GET {endpoint.url} answered {response.status_code} when the ownership read "
        f"failed. Expected 503.\nBody: {response.text[:400]!r}"
    )
    assert metrics.calls == [], (
        "an unresolved ownership check must not fall through to the read"
    )
    assert str(MARKER) not in response.text


# ---------------------------------------------------------------------------
# 5. Structural guard: every metrics handler carries the gate
# ---------------------------------------------------------------------------

#: The gate's name, as the handlers call it. Spelled once so renaming it is a two-file edit
#: rather than a silent loss of coverage.
GATE = "_assert_owns_strategy"


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_handler_source_calls_the_ownership_gate(endpoint: Endpoint):
    """Read out of the router's AST, so it holds without a request.

    The request-level tests above can only cover handlers this file already knows about.
    This one is what makes a *seventh* metrics endpoint fail loudly if it ships without a
    gate, via :func:`test_every_metrics_handler_is_covered_here`.
    """
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8", errors="replace"))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == endpoint.handler
    ]
    assert len(handlers) == 1, (
        f"expected exactly one {endpoint.handler} in {ROUTER_SOURCE.name}, found "
        f"{len(handlers)}"
    )

    gate_calls = [
        node
        for node in ast.walk(handlers[0])
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == GATE)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == GATE)
        )
    ]
    assert gate_calls, (
        f"{endpoint.handler} does not call {GATE}(). It reads a telemetry store keyed on "
        f"strategy_id with no RLS behind it, so without that call any authenticated caller "
        f"is answered this strategy's figures."
    )


def test_every_metrics_handler_is_covered_here():
    """A guard on the parametrisation.

    The defect was a family of six identical handlers. If a seventh is added that calls a
    ``MetricsService`` method, it must be listed above rather than shipping unguarded — so
    the router's own source decides the expected set, not this list.
    """
    from backend_app.backend.metrics_service import MetricsService

    service_methods = {
        name
        for name, member in inspect.getmembers(MetricsService, inspect.isfunction)
        if not name.startswith("_")
    }
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8", errors="replace"))
    calling_handlers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in service_methods
            for inner in ast.walk(node)
        )
    }
    covered = {endpoint.handler for endpoint in ENDPOINTS}
    assert calling_handlers <= covered, (
        f"{ROUTER_SOURCE.name} has handler(s) {sorted(calling_handlers - covered)} calling "
        f"a MetricsService method with no tenant-isolation coverage in this file. Add them "
        f"to ENDPOINTS above and give them the {GATE}() call."
    )
