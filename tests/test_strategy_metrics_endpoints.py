"""
tests/test_strategy_metrics_endpoints.py

The five siblings of ``GET /api/strategies/{strategy_id}/risk-metrics`` — the same
revert-detector, and the same no-leak assertion, for the five metrics endpoints that
carried the identical call-site defect.

WHAT WAS WRONG
--------------
``backend_app/routers/strategy_operations.py`` declared five handlers that each called a
``MetricsService`` method with ``user=user``:

    performance         -> get_strategy_performance(user=user, strategy_id=..., time_range=...)
    equity-curve        -> get_equity_curve(user=user, strategy_id=..., days=...)
    monthly-returns     -> get_monthly_returns(user=user, strategy_id=...)
    daily-returns       -> get_daily_returns(user=user, strategy_id=..., days=...)
    execution-metrics   -> get_execution_metrics(user=user, strategy_id=..., time_range=...)

Every one of those methods is declared ``(self, user_id: str, strategy_id: str, ...)`` and
none of them accepts ``user``. So every request raised ``TypeError`` at the call site,
before any read happened; each handler's ``except Exception`` turned that into
``HTTPException(500, {"error": "<CODE>", "message": str(e)})``; and the result was that all
five endpoints answered **500 to every caller**, owner included, with the Python signature
of an internal service method in the body. Captured before the fix:

    GET /api/strategies/{id}/performance        -> 500 "MetricsService.get_strategy_performance() got an unexpected keyword argument 'user'"
    GET /api/strategies/{id}/equity-curve       -> 500 "MetricsService.get_equity_curve() got an unexpected keyword argument 'user'"
    GET /api/strategies/{id}/monthly-returns    -> 500 "MetricsService.get_monthly_returns() got an unexpected keyword argument 'user'"
    GET /api/strategies/{id}/daily-returns      -> 500 "MetricsService.get_daily_returns() got an unexpected keyword argument 'user'"
    GET /api/strategies/{id}/execution-metrics  -> 500 "MetricsService.get_execution_metrics() got an unexpected keyword argument 'user'"

WHY IT WENT UNNOTICED
---------------------
``backend_app/backend/strategy_service._get_strategy_performance`` calls the same method
**positionally** (``get_strategy_performance(user["id"], strategy_id)``) and has always
worked. The service layer was fine where the HTTP layer was not, so anything exercising
strategies through the service saw correct figures while every direct HTTP caller saw a 500.

WHAT IS ASSERTED HERE
---------------------
Per endpoint, the same three things the risk-metrics file asserts for its own route:

1. **The call site binds against the declared signature.** Parsed out of the router
   module's own source with ``ast`` and bound with ``inspect.signature``, so re-introducing
   ``user=`` — or any other keyword the method does not declare — fails here without a
   request being made. This is the test that fails on a revert.
2. **An owned strategy is answered something other than 500**, twice over: once against the
   real ``MetricsService`` with only its QuestDB client doubled, which proves the real
   method body runs and its figures reach the wire; and once against a double whose
   signature is asserted *identical* to the real method's, which is what makes the caller's
   identifier observable — the real methods do not record what they were passed.
3. **A read that genuinely fails carries no Python type name and no signature text** on the
   wire. Asserted on ``response.text``, the whole raw body, so a leak relocated into
   ``details`` or into an envelope key would still be caught.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
Like the risk-metrics route, none of these five resolves ownership: they read by
``strategy_id`` and never filter on the caller. Test 2 says "not 500" and says nothing
whatever about access control, so it cannot be read as a licence for a missing 403. That
gap stays registered where it already is and is untouched by this file.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.metrics_service import MetricsService
from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import strategy_operations

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_SOURCE = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

#: ``_safe_id`` in the metrics service accepts ``[A-Za-z0-9_-]{1,128}``, which a UUID satisfies.
OWNER_ID = "66666666-6666-4666-8666-666666666666"
STRATEGY_ID = "77777777-7777-4777-8777-777777777777"
OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "owner@test.vyomquant.io",
    "role": "authenticated",
    "access_token": "token-for-the-rls-scoped-client",
}

#: Deliberately not a round number and not a threshold: an assertion on this value cannot
#: pass by coincidence against a default, a zero or a limit.
MARKER = 8675.309


# ---------------------------------------------------------------------------
# The five endpoints under test
# ---------------------------------------------------------------------------

class Endpoint:
    """One route, its handler, the service method behind it, and its failure code."""

    def __init__(
        self,
        suffix: str,
        handler: str,
        service_method: str,
        error_code: str,
        expected_kwargs: Dict[str, Any],
    ) -> None:
        self.suffix = suffix
        self.handler = handler
        self.service_method = service_method
        self.error_code = error_code
        #: What the handler must hand the service for a bare ``GET`` with no query string,
        #: i.e. every query parameter left at its declared default.
        self.expected_kwargs = expected_kwargs

    @property
    def url(self) -> str:
        return f"/api/strategies/{STRATEGY_ID}/{self.suffix}"

    def __repr__(self) -> str:  # pytest node id
        return self.suffix


ENDPOINTS: Tuple[Endpoint, ...] = (
    Endpoint(
        "performance",
        "get_strategy_performance",
        "get_strategy_performance",
        "PERFORMANCE_FETCH_FAILED",
        {"user_id": OWNER_ID, "strategy_id": STRATEGY_ID, "time_range": "1d"},
    ),
    Endpoint(
        "equity-curve",
        "get_strategy_equity_curve",
        "get_equity_curve",
        "EQUITY_CURVE_FETCH_FAILED",
        {"user_id": OWNER_ID, "strategy_id": STRATEGY_ID, "days": 30},
    ),
    Endpoint(
        "monthly-returns",
        "get_strategy_monthly_returns",
        "get_monthly_returns",
        "MONTHLY_RETURNS_FETCH_FAILED",
        {"user_id": OWNER_ID, "strategy_id": STRATEGY_ID},
    ),
    Endpoint(
        "daily-returns",
        "get_strategy_daily_returns",
        "get_daily_returns",
        "DAILY_RETURNS_FETCH_FAILED",
        {"user_id": OWNER_ID, "strategy_id": STRATEGY_ID, "days": 30},
    ),
    Endpoint(
        "execution-metrics",
        "get_strategy_execution_metrics",
        "get_execution_metrics",
        "EXECUTION_METRICS_FETCH_FAILED",
        {"user_id": OWNER_ID, "strategy_id": STRATEGY_ID, "time_range": "1d"},
    ),
)

BY_SUFFIX = {endpoint.suffix: endpoint for endpoint in ENDPOINTS}


def test_all_five_metrics_endpoints_are_covered():
    """A guard on the parametrisation itself.

    The defect was a *family* of identical call sites. If a sixth metrics handler is added
    to the router that calls a ``MetricsService`` method with keywords, it has to be added
    here too rather than shipping unguarded, so the router's own source is what decides the
    expected set.
    """
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8", errors="replace"))
    service_methods = {
        name
        for name, member in inspect.getmembers(MetricsService, inspect.isfunction)
        if not name.startswith("_")
    }
    calling_handlers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in service_methods
            and inner.keywords
            for inner in ast.walk(node)
        )
    }
    covered = {endpoint.handler for endpoint in ENDPOINTS}
    # ``get_strategy_risk_metrics`` is guarded by tests/test_strategy_risk_metrics_endpoint.py.
    covered.add("get_strategy_risk_metrics")
    assert calling_handlers <= covered, (
        f"{ROUTER_SOURCE.name} has handler(s) {sorted(calling_handlers - covered)} calling a "
        f"MetricsService method with keywords, and no file asserts their keywords bind "
        f"against the declared signature. That is exactly the shape of the defect this "
        f"file exists for."
    )


# ---------------------------------------------------------------------------
# 1. The call site binds against the declared signature
# ---------------------------------------------------------------------------

def _handler_call_keywords(endpoint: Endpoint) -> Tuple[List[str], bool]:
    """``(keyword names, saw_star_star)`` of the handler's service-method call.

    Read out of the router module's source rather than out of a mock's call record, so the
    assertion holds without a request and without a double that could disagree with the
    code.
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

    calls = [
        node
        for node in ast.walk(handlers[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == endpoint.service_method
    ]
    assert len(calls) == 1, (
        f"expected exactly one {endpoint.service_method} call inside {endpoint.handler}, "
        f"found {len(calls)}"
    )

    call = calls[0]
    assert not call.args, (
        f"{endpoint.handler} passes {len(call.args)} positional argument(s) to "
        f"{endpoint.service_method}; this assertion reads the keywords, so a positional "
        f"call has to be reviewed by hand"
    )
    names = [kw.arg for kw in call.keywords if kw.arg is not None]
    star_star = any(kw.arg is None for kw in call.keywords)
    return names, star_star


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_handler_passes_only_keywords_the_declared_signature_accepts(endpoint: Endpoint):
    """The revert-detector. Restore ``user=user`` and this fails without a request.

    Bound against ``inspect.signature`` rather than compared to a hard-coded name list, so
    the test tracks the service's declaration instead of keeping a second copy of it:
    rename the parameter on the service and the two must be changed together, which is the
    point.
    """
    names, star_star = _handler_call_keywords(endpoint)
    assert not star_star, (
        f"{endpoint.handler} forwards ``**kwargs`` to {endpoint.service_method}; the "
        f"keywords cannot be checked statically and the signature agreement is "
        f"unenforceable"
    )

    signature = inspect.signature(getattr(MetricsService, endpoint.service_method))
    try:
        # ``self`` first, then the handler's keywords. Sentinels: only the binding is under
        # test, not the values.
        signature.bind(object(), **{name: object() for name in names})
    except TypeError as exc:
        raise AssertionError(
            f"{endpoint.handler} calls MetricsService.{endpoint.service_method} with "
            f"keyword(s) {names}, which the declared signature {signature} does not "
            f"accept: {exc}. Every request to GET {endpoint.url} therefore raises TypeError "
            f"before any read, and the handler's `except Exception` reports it as a 500 to "
            f"every caller, owner included."
        ) from exc


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_declared_signature_asks_for_the_caller_by_id(endpoint: Endpoint):
    """A guard on the guard above.

    The binding test would also pass if the *service* had been changed to accept ``user``.
    That would be a different fix — one that hands a whole authenticated-session dict to a
    module that builds QuestDB query text — so the direction of the agreement is pinned:
    the service asks for an identifier, and the router is what supplies it.
    """
    parameters = inspect.signature(
        getattr(MetricsService, endpoint.service_method)
    ).parameters
    assert "user_id" in parameters, (
        f"MetricsService.{endpoint.service_method} no longer declares ``user_id``; the "
        f"router's call site and this file were written against a signature that asks for "
        f"the caller's identifier"
    )
    assert "user" not in parameters, (
        f"MetricsService.{endpoint.service_method} now declares ``user``; if that is "
        f"intended, this file's premise needs rewriting rather than adjusting, and the "
        f"risk-metrics route has to move with it"
    )


def test_the_service_layer_call_site_stays_positional():
    """Why the HTTP defect survived: the service layer never had it.

    ``strategy_service._get_strategy_performance`` passes ``user["id"]`` positionally, so it
    kept working while every HTTP caller got a 500. Pinned here so a later "tidy-up" that
    converts it to ``user=`` keywords — reintroducing the same defect one layer down — fails.
    """
    service_source = REPO_ROOT / "backend_app" / "backend" / "strategy_service.py"
    tree = ast.parse(service_source.read_text(encoding="utf-8", errors="replace"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_strategy_performance"
    ]
    assert calls, (
        f"{service_source.name} no longer calls MetricsService.get_strategy_performance; "
        f"this assertion was written against the call that made the HTTP defect invisible"
    )
    signature = inspect.signature(MetricsService.get_strategy_performance)
    for call in calls:
        names = [kw.arg for kw in call.keywords if kw.arg is not None]
        assert not any(kw.arg is None for kw in call.keywords), (
            f"{service_source.name} forwards ``**kwargs`` to get_strategy_performance; the "
            f"agreement with the declared signature is no longer checkable statically"
        )
        try:
            signature.bind(
                *([object()] + [object()] * len(call.args)),
                **{name: object() for name in names},
            )
        except TypeError as exc:
            raise AssertionError(
                f"{service_source.name} calls MetricsService.get_strategy_performance with "
                f"{len(call.args)} positional argument(s) and keyword(s) {names}, which the "
                f"declared signature {signature} does not accept: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# 2. An owned strategy is answered something other than 500
# ---------------------------------------------------------------------------

class _StubTelemetry:
    """The QuestDB client, and nothing else. The real query text is what reaches it.

    One column set serves all five reads: each of the five method bodies zips the returned
    ``columns`` against the returned row, so ``MARKER`` surfaces in whichever field the
    endpoint under test projects it into.
    """

    def __init__(self) -> None:
        self.queries: List[str] = []

    async def execute_query(self, query: str, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        self.queries.append(query)
        return {
            "columns": [
                {"name": "timestamp"},
                {"name": "total_pnl"},
                {"name": "total_orders"},
            ],
            "dataset": [[None, MARKER, MARKER]],
        }


class _RecordingMetricsService:
    """A double that records what the router passed it.

    Each signature is asserted **identical** to the real method's in
    :func:`test_the_recording_double_has_the_real_signature`, so none of them can drift into
    accepting something the real service would refuse.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def get_strategy_performance(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        self.calls.append(
            {"user_id": user_id, "strategy_id": strategy_id, "time_range": time_range}
        )
        return {"marker": MARKER}

    async def get_equity_curve(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        self.calls.append({"user_id": user_id, "strategy_id": strategy_id, "days": days})
        return [{"marker": MARKER}]

    async def get_monthly_returns(self, user_id: str, strategy_id: str) -> List[Dict]:
        self.calls.append({"user_id": user_id, "strategy_id": strategy_id})
        return [{"marker": MARKER}]

    async def get_daily_returns(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        self.calls.append({"user_id": user_id, "strategy_id": strategy_id, "days": days})
        return [{"marker": MARKER}]

    async def get_execution_metrics(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        self.calls.append(
            {"user_id": user_id, "strategy_id": strategy_id, "time_range": time_range}
        )
        return {"marker": MARKER}


class _RaisingMetricsService:
    """Reads that genuinely fail, with the exact text the old bodies used to echo."""

    @staticmethod
    def leak(service_method: str) -> str:
        return (
            f"MetricsService.{service_method}() got an unexpected keyword argument 'user'"
        )

    async def get_strategy_performance(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        raise TypeError(self.leak("get_strategy_performance"))

    async def get_equity_curve(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        raise TypeError(self.leak("get_equity_curve"))

    async def get_monthly_returns(self, user_id: str, strategy_id: str) -> List[Dict]:
        raise TypeError(self.leak("get_monthly_returns"))

    async def get_daily_returns(
        self, user_id: str, strategy_id: str, days: int = 30
    ) -> List[Dict]:
        raise TypeError(self.leak("get_daily_returns"))

    async def get_execution_metrics(
        self, user_id: str, strategy_id: str, time_range: str = "1d"
    ) -> Dict:
        raise TypeError(self.leak("get_execution_metrics"))


@pytest.fixture
def owner_client():
    """A ``TestClient`` authenticated as the strategy's owner.

    ``raise_server_exceptions=False`` so a 500 is a response to assert on rather than an
    exception propagating out of the client.
    """
    app.dependency_overrides[get_current_user] = lambda: dict(OWNER)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _install(monkeypatch, service: Any) -> None:
    """Replace the router's one seam onto the metrics service."""

    async def _get_metrics_service() -> Any:
        return service

    monkeypatch.setattr(
        strategy_operations, "get_metrics_service", _get_metrics_service
    )


def _callable_shape(function: Any) -> Tuple[Tuple[str, Any, Any], ...]:
    """``(name, kind, default)`` per parameter.

    Annotations are excluded deliberately: this module carries
    ``from __future__ import annotations``, so the doubles' are strings while the service's
    are evaluated objects. What has to agree is which calls each accepts, and that is
    decided by the names, the kinds and the defaults.
    """
    return tuple(
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(function).parameters.items()
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_recording_double_has_the_real_signature(endpoint: Endpoint):
    """Otherwise the recording test below would be a test of the double."""
    double = _callable_shape(
        getattr(_RecordingMetricsService, endpoint.service_method)
    )
    real = _callable_shape(getattr(MetricsService, endpoint.service_method))
    assert double == real, (
        f"the recording double's {endpoint.service_method} no longer accepts exactly what "
        f"MetricsService.{endpoint.service_method} accepts; a call the double takes could "
        f"be one the real service refuses.\n  double: {double}\n  real:   {real}"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_an_owned_strategys_metrics_are_answered_not_500(
    endpoint: Endpoint, owner_client, monkeypatch
):
    """2a. The real service, with only its QuestDB client doubled.

    The real method body runs, so this fails if the call site cannot reach it — and the
    figure on the wire is the one the telemetry row carried, not a zero from the empty-data
    fallback.
    """
    telemetry = _StubTelemetry()
    service = MetricsService()
    service._telemetry = telemetry  # noqa: SLF001 - the one seam this environment lacks
    _install(monkeypatch, service)

    response = owner_client.get(endpoint.url)

    assert response.status_code != 500, (
        f"GET {endpoint.url} answered 500 to the strategy's OWNER. "
        f"Body: {response.text[:400]!r}"
    )
    assert response.status_code == 200, response.text
    assert str(MARKER) in response.text, (
        f"the figure on the wire is not the one the telemetry row carried, so the real "
        f"{endpoint.service_method} body did not produce this answer. "
        f"Body: {response.text[:400]!r}"
    )
    assert telemetry.queries, "the handler answered without reaching the read at all"
    assert STRATEGY_ID in telemetry.queries[0]


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_the_callers_identifier_is_what_reaches_the_service(
    endpoint: Endpoint, owner_client, monkeypatch
):
    """2b. The double records the arguments the real methods throw away."""
    service = _RecordingMetricsService()
    _install(monkeypatch, service)

    response = owner_client.get(endpoint.url)

    assert response.status_code == 200, response.text
    assert service.calls == [endpoint.expected_kwargs], (
        f"the service was called with {service.calls!r}; the handler must pass the "
        f"authenticated caller's own id as ``user_id``, the path's strategy id as "
        f"``strategy_id``, and each query parameter at its declared default. Expected "
        f"{[endpoint.expected_kwargs]!r}"
    )
    assert str(MARKER) in response.text, (
        f"the service's answer did not reach the response body. "
        f"Body: {response.text[:400]!r}"
    )


# ---------------------------------------------------------------------------
# 3. The failure body carries no exception text
# ---------------------------------------------------------------------------

#: Shapes that identify a Python object or a function signature. Not one of them is a fact
#: about the caller's own request, so not one of them belongs in a response body.
FORBIDDEN_IN_A_PUBLIC_BODY = (
    "TypeError",
    "ValueError",
    "RuntimeError",
    "Exception",
    "Traceback",
    "MetricsService",
    "unexpected keyword argument",
    "positional argument",
)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=repr)
def test_a_failed_read_leaks_no_python_type_name_or_signature_text(
    endpoint: Endpoint, owner_client, monkeypatch
):
    """The 500 says the read failed and stops there.

    Asserted on the whole raw body: a leak relocated into ``details``, or into an envelope
    key this file does not name, is still on the wire and is still caught.
    """
    _install(monkeypatch, _RaisingMetricsService())

    response = owner_client.get(endpoint.url)

    assert response.status_code == 500, (
        f"a read that raised was answered {response.status_code}; the failure must not be "
        f"reported as a success. Body: {response.text[:400]!r}"
    )
    body = response.text
    assert endpoint.error_code in body, (
        f"the failure carries no stable machine-readable code. Body: {body[:400]!r}"
    )
    leak = _RaisingMetricsService.leak(endpoint.service_method)
    assert leak not in body, (
        f"the exception's own message is on the wire; the handler is echoing ``str(e)`` "
        f"again, which is how the service method's Python signature reached the client. "
        f"Body: {body[:400]!r}"
    )
    assert endpoint.service_method not in body, (
        f"the 500 body names the internal service method {endpoint.service_method!r}. "
        f"Body: {body[:400]!r}"
    )
    for token in FORBIDDEN_IN_A_PUBLIC_BODY:
        assert token not in body, (
            f"the 500 body carries {token!r}, which names a Python type, a Python "
            f"signature or a traceback rather than a fact about the request. "
            f"Body: {body[:400]!r}"
        )
