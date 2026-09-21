"""
tests/test_strategy_risk_metrics_endpoint.py

``GET /api/strategies/{strategy_id}/risk-metrics`` — the revert-detector for the call-site
signature mismatch, and for the exception text that used to travel with the 500.

WHAT WAS WRONG
--------------
``backend_app/routers/strategy_operations.get_strategy_risk_metrics`` called

    await metrics_service.get_risk_metrics(user=user, strategy_id=strategy_id)

while ``backend_app/backend/metrics_service.MetricsService.get_risk_metrics`` is declared

    async def get_risk_metrics(self, user_id: str, strategy_id: str) -> Dict

so every request raised ``TypeError: MetricsService.get_risk_metrics() got an unexpected
keyword argument 'user'`` before a single row was read. The handler's ``except Exception``
turned that into ``HTTPException(500, {"error": "RISK_METRICS_FETCH_FAILED", "message":
str(e)})``, which means two things at once: the endpoint answered **500 to every caller**,
owner included, and the 500 body carried the Python signature of an internal service method
out to the client.

WHAT IS ASSERTED HERE
---------------------
1. **The call site binds against the declared signature.** Parsed out of the router module's
   own source with ``ast`` and bound with ``inspect.signature`` — so re-introducing ``user=``
   (or any other keyword the method does not declare) fails here without a request being
   made. This is the test that fails on a revert.
2. **An owned strategy is answered something other than 500**, twice over: once against the
   real ``MetricsService`` with only its QuestDB client doubled (the one thing this
   environment has no instance of), which proves the real method body runs and its figures
   reach the wire; and once against a double whose signature is asserted *identical* to the
   real method's, which is what makes the caller's identifier observable — the real method
   does not record what it was passed.
3. **A read that genuinely fails carries no Python type name and no signature text** on the
   wire. Asserted on ``response.text``, the whole raw body, rather than on one field, so a
   leak moved into ``details`` or an envelope key would still be caught.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
This route resolves **no ownership at all** — it reads ``strategy_risk`` by ``strategy_id``
and never filters on the caller. A subscriber therefore gets the owner's answer instead of
the 403 Requirement 12.7 calls for. That defect is untouched by this file and stays
registered in ``tests/test_subscriber_restricted_operations.py``'s ``PENDING`` under
``view_risk_config.risk_metrics``. Test 2 below says "not 500" and says nothing whatever
about access control, so it cannot be read as a licence for the missing 403.
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

from backend_app.backend.metrics_service import MetricsService
from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import strategy_operations

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_SOURCE = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

HANDLER = "get_strategy_risk_metrics"
SERVICE_METHOD = "get_risk_metrics"

#: The owner of the strategy under test. ``_safe_id`` in the metrics service accepts
#: ``[A-Za-z0-9_-]{1,128}``, which a UUID satisfies.
OWNER_ID = "66666666-6666-4666-8666-666666666666"
STRATEGY_ID = "77777777-7777-4777-8777-777777777777"
OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "owner@test.vyomquant.io",
    "role": "authenticated",
    "access_token": "token-for-the-rls-scoped-client",
}

#: Deliberately not a round number and not a threshold: an assertion on this value cannot
#: pass by coincidence against a default or a limit.
DRAWDOWN = -13.37


def _url() -> str:
    return f"/api/strategies/{STRATEGY_ID}/risk-metrics"


# ---------------------------------------------------------------------------
# 1. The call site binds against the declared signature
# ---------------------------------------------------------------------------

def _handler_call_keywords() -> Tuple[List[str], bool]:
    """``(keyword names, saw_star_star)`` of the handler's ``get_risk_metrics(...)`` call.

    Read out of the router module's source rather than out of a mock's call record, so the
    assertion holds without a request and without a double that could disagree with the
    code.
    """
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8", errors="replace"))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == HANDLER
    ]
    assert len(handlers) == 1, (
        f"expected exactly one {HANDLER} in {ROUTER_SOURCE.name}, found {len(handlers)}"
    )

    calls = [
        node
        for node in ast.walk(handlers[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == SERVICE_METHOD
    ]
    assert len(calls) == 1, (
        f"expected exactly one {SERVICE_METHOD} call inside {HANDLER}, found {len(calls)}"
    )

    call = calls[0]
    assert not call.args, (
        f"{HANDLER} passes {len(call.args)} positional argument(s) to {SERVICE_METHOD}; this "
        f"assertion reads the keywords, so a positional call has to be reviewed by hand"
    )
    names = [kw.arg for kw in call.keywords if kw.arg is not None]
    star_star = any(kw.arg is None for kw in call.keywords)
    return names, star_star


def test_the_handler_passes_only_keywords_the_declared_signature_accepts():
    """The revert-detector. Restore ``user=user`` and this fails without a request.

    Bound against ``inspect.signature`` rather than compared to a hard-coded name list, so
    the test tracks the service's declaration instead of a second copy of it: rename the
    parameter on the service and the two must be changed together, which is the point.
    """
    names, star_star = _handler_call_keywords()
    assert not star_star, (
        f"{HANDLER} forwards ``**kwargs`` to {SERVICE_METHOD}; the keywords cannot be "
        f"checked statically and the signature agreement is unenforceable"
    )

    signature = inspect.signature(getattr(MetricsService, SERVICE_METHOD))
    try:
        # ``self`` first, then the handler's keywords. Sentinels: only the binding is
        # under test, not the values.
        signature.bind(object(), **{name: object() for name in names})
    except TypeError as exc:
        raise AssertionError(
            f"{HANDLER} calls MetricsService.{SERVICE_METHOD} with keyword(s) {names}, which "
            f"the declared signature {signature} does not accept: {exc}. Every request to "
            f"GET /api/strategies/{{strategy_id}}/risk-metrics therefore raises TypeError "
            f"before any read, and the handler's `except Exception` reports it as a 500 to "
            f"every caller, owner included."
        ) from exc


def test_the_declared_signature_asks_for_the_caller_by_id():
    """A guard on the guard above.

    The binding test would also pass if the *service* had been changed to accept ``user``.
    That would be a different fix — one that hands a whole authenticated-session dict to a
    module that builds QuestDB query text — so the direction of the agreement is pinned:
    the service asks for an identifier, and the router is what supplies it.
    """
    parameters = inspect.signature(getattr(MetricsService, SERVICE_METHOD)).parameters
    assert "user_id" in parameters, (
        f"MetricsService.{SERVICE_METHOD} no longer declares ``user_id``; the router's call "
        f"site and this file were written against a signature that asks for the caller's "
        f"identifier"
    )
    assert "user" not in parameters, (
        f"MetricsService.{SERVICE_METHOD} now declares ``user``; if that is intended, the "
        f"five sibling metrics endpoints have to move with it, and this file's premise "
        f"needs rewriting rather than adjusting"
    )


# ---------------------------------------------------------------------------
# 2. An owned strategy is answered something other than 500
# ---------------------------------------------------------------------------

class _StubTelemetry:
    """The QuestDB client, and nothing else. The real query text is what reaches it."""

    def __init__(self) -> None:
        self.queries: List[str] = []

    async def execute_query(self, query: str, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        self.queries.append(query)
        return {
            "columns": [
                {"name": "max_drawdown"},
                {"name": "current_exposure"},
                {"name": "kill_switch_active"},
            ],
            "dataset": [[DRAWDOWN, 4200.0, False]],
        }


class _RecordingMetricsService:
    """A double that records what the router passed it.

    Its ``get_risk_metrics`` signature is asserted **identical** to the real method's in
    :func:`test_the_recording_double_has_the_real_signature`, so it cannot drift into
    accepting something the real service would refuse.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def get_risk_metrics(self, user_id: str, strategy_id: str) -> Dict:
        self.calls.append({"user_id": user_id, "strategy_id": strategy_id})
        return {"max_drawdown": DRAWDOWN}


class _RaisingMetricsService:
    """A read that genuinely fails, with the exact text the old body used to echo."""

    LEAK = (
        "MetricsService.get_risk_metrics() got an unexpected keyword argument 'user'"
    )

    async def get_risk_metrics(self, user_id: str, strategy_id: str) -> Dict:
        raise TypeError(self.LEAK)


@pytest.fixture
def owner_client(monkeypatch):
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
    ``from __future__ import annotations``, so the double's are strings while the service's
    are evaluated objects. What has to agree is which calls each accepts, and that is
    decided by the names, the kinds and the defaults.
    """
    return tuple(
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(function).parameters.items()
    )


def test_the_recording_double_has_the_real_signature():
    """Otherwise test 2b would be a test of the double."""
    double = _callable_shape(_RecordingMetricsService.get_risk_metrics)
    real = _callable_shape(getattr(MetricsService, SERVICE_METHOD))
    assert double == real, (
        "the recording double's get_risk_metrics no longer accepts exactly what "
        f"MetricsService.{SERVICE_METHOD} accepts; a call the double takes could be one the "
        f"real service refuses.\n  double: {double}\n  real:   {real}"
    )


def test_an_owned_strategys_risk_metrics_are_answered_not_500(owner_client, monkeypatch):
    """2a. The real service, with only its QuestDB client doubled.

    The real ``get_risk_metrics`` body runs, so this fails if the call site cannot reach it —
    and the figures on the wire are the ones the telemetry row carried, not zeros.
    """
    telemetry = _StubTelemetry()
    service = MetricsService()
    service._telemetry = telemetry  # noqa: SLF001 - the one seam this environment lacks
    _install(monkeypatch, service)

    response = owner_client.get(_url())

    assert response.status_code != 500, (
        f"GET {_url()} answered 500 to the strategy's OWNER. Body: {response.text[:400]!r}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["strategy_id"] == STRATEGY_ID
    assert body["risk_metrics"]["max_drawdown"] == DRAWDOWN, (
        "the figures on the wire are not the ones the telemetry row carried, so the real "
        "method body did not produce this answer"
    )
    assert telemetry.queries, "the handler answered without reaching the read at all"
    assert STRATEGY_ID in telemetry.queries[0]


def test_the_callers_identifier_is_what_reaches_the_service(owner_client, monkeypatch):
    """2b. The double records the arguments the real method throws away."""
    service = _RecordingMetricsService()
    _install(monkeypatch, service)

    response = owner_client.get(_url())

    assert response.status_code == 200, response.text
    assert service.calls == [{"user_id": OWNER_ID, "strategy_id": STRATEGY_ID}], (
        f"the service was called with {service.calls!r}; the handler must pass the "
        f"authenticated caller's own id as ``user_id`` and the path's strategy id as "
        f"``strategy_id``"
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
    "get_risk_metrics",
    "unexpected keyword argument",
    "positional argument",
    "self",
)


def test_a_failed_read_leaks_no_python_type_name_or_signature_text(owner_client, monkeypatch):
    """The 500 says the read failed and stops there.

    Asserted on the whole raw body: a leak relocated into ``details``, or into an envelope
    key this file does not name, is still on the wire and is still caught.
    """
    _install(monkeypatch, _RaisingMetricsService())

    response = owner_client.get(_url())

    assert response.status_code == 500, (
        f"a read that raised was answered {response.status_code}; the failure must not be "
        f"reported as a success. Body: {response.text[:400]!r}"
    )
    body = response.text
    assert "RISK_METRICS_FETCH_FAILED" in body, (
        f"the failure carries no stable machine-readable code. Body: {body[:400]!r}"
    )
    assert _RaisingMetricsService.LEAK not in body, (
        "the exception's own message is on the wire; the handler is echoing ``str(e)`` "
        "again, which is how the service method's Python signature reached the client. "
        f"Body: {body[:400]!r}"
    )
    for token in FORBIDDEN_IN_A_PUBLIC_BODY:
        assert token not in body, (
            f"the 500 body carries {token!r}, which names a Python type, a Python "
            f"signature or a traceback rather than a fact about the request. "
            f"Body: {body[:400]!r}"
        )
