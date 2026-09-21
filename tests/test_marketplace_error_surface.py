"""
tests/test_marketplace_error_surface.py

Task 16.5 of ``marketplace-subscriptions-paper-trading``: the Marketplace error surface. Five
guarantees, all tested mechanically, none of them by inspection.

WHAT IS ASSERTED
----------------
1. **No read failure becomes a success body** (Requirements 1.5, 1.7). For each of FOUR failure
   shapes at the Persistence_Layer — a connection failure, a query timeout, an undefined column
   (PostgreSQL ``42703``) and a permission denial — every Marketplace *read* endpoint must answer
   500 or 503, must carry the stable ``MARKETPLACE_READ_FAILED`` code, and must carry NO numeric
   payload. A read failure reported as HTTP 200 with every figure zero — ``creator_analytics``'
   ``except Exception: return {…zeros…}``, which shows a creator a fabricated ``0.0`` instead of
   an error — is exactly the substitution these two requirements forbid, and it fails here.

   The endpoint list is **enumerated from ``app.router.routes``**, not written by hand: every
   ``GET`` route whose handler lives in ``backend_app.routers.library`` is covered, so a read
   endpoint added later is covered the day it is added, with no edit to this file.

2. **No public message leaks an internal** (Requirements 2.10, 4.8, 22.9). Every
   ``errors.PUBLIC_MESSAGE_FOR_CODE`` sentence is free of ``SELECT``, ``library_strategies``,
   ``strategy_backtests``, ``Traceback``, ``psycopg``, ``42703``, ``23505``, and of any digit
   sequence appearing in ``evidence_validator.THRESHOLDS`` or ``pricing_evaluator.WEIGHTS``. The
   threshold-digit rule is what stops an eligibility rejection from disclosing the internal
   validation thresholds it was judged against (Requirement 2.10's exclusion list).

3. **No error body carries a stack trace, a database error string, query text, an internal
   filesystem path, or an internal identifier other than the caller's own resources**
   (Requirement 22.9). Asserted twice over: on the live 5xx bodies of every enumerated endpoint,
   and on a synthetic ``details`` payload poisoned with all of those shapes, which proves it is
   ``errors.redact_details`` — not the discipline of each raiser — that makes the guarantee hold.

4. **The envelope shape is exactly** ``{"error": {"code", "message", "details"}, "request_id"}``
   and the ``code`` values are **stable**: drawn from the declared catalogue in
   ``backend_app/backend/marketplace/errors.py``, never assembled at a call site. The catalogue is
   the gate, not a convention — ``StructuredError.__init__`` refuses a code it does not contain,
   which is what makes the two dynamic code sources (``_SERVICE_CODE_TO_CATALOGUE`` and
   ``entitlement_resolver.WIRE_CODE_FOR_REASON``) safe; both mappings are checked against the
   catalogue here as well.

5. **A read that COMPLETED still answers exactly what it answered before** (Requirement 30.5).
   Closing a swallowing ``except`` changes a live read endpoint, and the risk of that change is
   not that the failure path stays wrong — the four sections above cover that — but that the
   SUCCESS path moves. So each of the nine endpoints task 21.3 changed is driven a second time
   against a client whose reads *succeed*, and its ``(status, body)`` is pinned literally in
   :data:`SUCCESS_CASES`. Three of them are pinned twice: once for a read that returned rows and
   once for a read that COMPLETED and matched nothing, because "no rows" is the outcome the old
   ``except`` blocks were indistinguishable from and it is the one that must NOT have become a
   503. ``get_own_submission``'s empty read still answers 404 ``MARKETPLACE_SUBMISSION_NOT_FOUND``
   and ``check_deployment_permission_endpoint``'s still answers a 200 denial — Requirement 21.4's
   indistinguishability and the advisory check's real refusal, both unmoved.

HARNESS
-------
A FastAPI ``TestClient`` (``raise_server_exceptions=False``, so an unhandled 500 becomes a
response instead of propagating) with ``backend_app.routers.library._build_service_client`` and
``._get_service_client`` patched to a fake service-role client whose every ``.execute()`` raises
one of the four failure shapes. Redis is patched to miss, and ``_CACHED_CATEGORIES`` is reset, so
no endpoint can answer from a cache instead of reaching the read under test. Every authentication
dependency is overridden with a fixed caller so the route body runs; the READ is what is being
tested, not the guard in front of it.

Each ``(endpoint, failure mode)`` response is fetched ONCE and memoised, for two reasons: the
routes are rate-limited and this file would otherwise spend its own budget, and the fake client is
deterministic so a second call could only produce the same answer.

A fresh exception INSTANCE is raised per call. Re-raising one instance across requests chains
``__context__`` from the previous request onto the next, which turns a later traceback into a
transcript of every earlier one — noise that looks like a leak.

THE PENDING REGISTRY (read this before "fixing" a failure here)
---------------------------------------------------------------
Zero of the nineteen enumerated read endpoints are pending. :data:`PENDING` is **EMPTY**: every
one of the nineteen that are already correct is asserted strictly, so a newly added read endpoint,
or a regression in any of them, fails immediately rather than being tolerated.

The registry held known Requirement 1.5 defects, each with the clauses it failed and the task
that owned the fix, marked ``xfail(strict=True)`` so that the moment one started passing THIS FILE
FAILED with ``XPASS`` — the signal to delete its entry, not to loosen an assertion. It went
eleven → nine when task 21.1 struck ``creator_analytics`` and ``subscriber_analytics``, and
nine → zero when task 21.3 closed the last of them:

  * ``get_strategy_reviews`` — was ``except Exception: return {"reviews": [], "total": 0}``, a
    zero-filled 200 that renders as "no reviews yet" for a strategy that may have hundreds;
  * ``get_categories`` — was ``except Exception: return {"categories": []}``, an empty 200 that
    renders as an empty marketplace, and cached it for ten minutes;
  * ``check_deployment_permission_endpoint`` — swallowed BOTH of the helper's reads and answered
    ``200 has_permission=false``, a **fabricated denial** a caller could not tell from a real one
    (Requirement 28.3: a fabricated denial is as much an invented figure as a fabricated balance);
  * ``my_library``, ``admin_pending_strategies``, ``get_subscription_status`` — answered 5xx, so
    no figure was fabricated, but through ``HTTPException(500, …)``: no stable machine-readable
    code, and the legacy envelope with the internal ``path`` member Requirement 22.9 excludes;
  * ``admin_submission_detail`` — three bare ``.execute()`` calls inside
    ``submission_service.admin_detail`` let the driver error ESCAPE the service, past a route that
    catches only ``SubmissionServiceError``, into ``main.py``'s catch-all and out as a generic 500;
  * ``admin_list_submissions`` and ``get_own_submission`` — were
    ``except Exception: raise MarketplaceError(MARKETPLACE_SUBMISSION_NOT_FOUND)``: a read failure
    answered **404**, telling an Admin_Reviewer the queue is empty and an owner their Submission
    does not exist, on the evidence of a query that never returned.

All nine now raise ``MARKETPLACE_READ_FAILED`` (503) for a read that DID NOT COMPLETE — and,
separately, for a response that carried no readable ``data``, because reading that as "no rows"
is the same fabrication by a quieter route (Requirement 28.5). ``creator_analytics`` is the
reference implementation of both halves. What did NOT change is the answer to a read that
COMPLETED: an empty result, an absent Listing, another owner's Submission and a caller with no
entitlement are answered exactly as before, which guarantee 5 pins per endpoint — so no genuine
404 became a 503 (Requirement 21.4) and no refusal became an admission.

Do NOT weaken an assertion to make a swallowing endpoint pass, and do NOT add an endpoint to
:data:`PENDING` to silence it. The registry is a list of known defects, not a list of exemptions;
it is empty, and the way to keep it empty is to fix the endpoint.
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user
from backend_app.core.subscription_dependencies import require_marketplace_access
from backend_app.main import app
from backend_app.backend.marketplace import entitlement_resolver as _entitlement_resolver
from backend_app.backend.marketplace import errors
from backend_app.backend.marketplace import evidence_validator
from backend_app.backend.marketplace import pricing_evaluator
from backend_app.routers import library as library_router


client = TestClient(app, raise_server_exceptions=False)

#: The module every Marketplace HTTP handler lives in. The enumeration below is scoped by this
#: rather than by a path prefix, so a route moved to another prefix is still covered and a route
#: from another domain mounted under ``/api/library`` is not silently claimed.
MARKETPLACE_ROUTER_MODULE = "backend_app.routers.library"

#: The value substituted for every path parameter. One identifier for all of them: the response
#: may legitimately echo it (it is the caller's own request), and assertion 3 allows exactly that.
PATH_PARAM_ID = "77777777-7777-7777-7777-777777777777"

#: The caller. A read failure must surface as an error for an authenticated caller exactly as it
#: does for an anonymous one, so the authenticated endpoints are driven with a plain user and the
#: admin endpoints with an admin; neither identity can turn a failed read into a success.
A_USER: Dict[str, Any] = {
    "id": "55555555-5555-5555-5555-555555555555",
    "email": "reader@test.vyomquant.io",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}
AN_ADMIN: Dict[str, Any] = {
    "id": "66666666-6666-6666-6666-666666666666",
    "email": "reviewer@test.vyomquant.io",
    "role": "admin",
    "app_metadata": {"role": "admin"},
    "user_metadata": {},
}

#: The identifiers a response body may echo: the caller's own user id, the admin's own id, and the
#: identifier the caller put in the path. Anything else is somebody else's (Requirement 22.9).
CALLER_OWN_IDENTIFIERS: FrozenSet[str] = frozenset(
    {A_USER["id"], AN_ADMIN["id"], PATH_PARAM_ID}
)


# ══════════════════════════════════════════════════════════════════════════
# The four Persistence_Layer failure shapes (Requirement 1.5)
# ══════════════════════════════════════════════════════════════════════════


class _UndefinedColumn(Exception):
    """The PostgreSQL ``42703`` an out-of-date ``.select(...)`` provokes.

    Shaped like a ``postgrest`` ``APIError``: a ``code`` attribute plus an ``args[0]`` dict, so a
    handler that inspects either sees a realistic driver error. The message deliberately carries
    the sqlstate and a real column name — the very things a response body must not echo, which
    assertion 3 checks for by name.
    """

    def __init__(self) -> None:
        self.code = "42703"
        self.message = "column library_strategies.monthly_price does not exist"
        super().__init__({"code": self.code, "message": self.message})


class _PermissionDenied(Exception):
    """A ``42501`` denial — a read the row-level policy refused."""

    def __init__(self) -> None:
        self.code = "42501"
        self.message = "permission denied for table library_strategies"
        super().__init__({"code": self.code, "message": self.message})


def _connection_failure() -> BaseException:
    return ConnectionError(
        'could not connect to server: Connection refused\n\tis the server running on host '
        '"db.internal.vyomquant.io" and accepting TCP/IP connections on port 5432?'
    )


def _query_timeout() -> BaseException:
    return TimeoutError(
        "canceling statement due to statement timeout while executing "
        "SELECT id, price_minor FROM library_strategies"
    )


#: name -> a FACTORY, not an instance. See the module docstring on ``__context__`` chaining.
FAILURE_MODES = {
    "connection_failure": _connection_failure,
    "query_timeout": _query_timeout,
    "undefined_column_42703": _UndefinedColumn,
    "permission_denied_42501": _PermissionDenied,
}

#: Every internal string the four failure shapes above carry into the handler. If any of these
#: reaches a client body, Requirement 22.9 is broken: a stack trace, a database error string,
#: query text, an internal host or an internal column name.
DRIVER_INTERNALS: Tuple[str, ...] = (
    "42703",
    "42501",
    "monthly_price",
    "library_strategies",
    "permission denied",
    "could not connect",
    "statement timeout",
    "db.internal.vyomquant.io",
    "5432",
    "connection refused",
)


# ══════════════════════════════════════════════════════════════════════════
# A fake service-role client whose every read raises the scripted failure
# ══════════════════════════════════════════════════════════════════════════


class _Verb:
    """One builder verb. Callable (``.eq("a", 1)``) and traversable (``.not_.is_(...)``)."""

    def __init__(self, query: "_RaisingQuery") -> None:
        self._query = query

    def __call__(self, *_a: Any, **_k: Any) -> "_RaisingQuery":
        return self._query

    def __getattr__(self, name: str) -> Any:
        return getattr(self._query, name)


class _RaisingQuery:
    """A fluent query builder that raises the moment ``.execute()`` is called.

    Every verb resolves to a ``_Verb``, so an arbitrary
    ``.table(...).select(...).eq(...).in_(...).not_.is_(...).order(...).range(...).single()``
    chain builds without error and only the terminal ``.execute()`` fails — which is where a real
    Persistence_Layer read fails.
    """

    def __init__(self, factory) -> None:
        self._factory = factory

    def __getattr__(self, _name: str) -> _Verb:
        return _Verb(self)

    def execute(self) -> Any:
        raise self._factory()


class _RaisingClient:
    """A service-role client whose every table, view and RPC read raises the scripted failure."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self.reads_attempted: List[str] = []

    def table(self, name: str) -> _RaisingQuery:
        self.reads_attempted.append(name)
        return _RaisingQuery(self._factory)

    def from_(self, name: str) -> _RaisingQuery:
        self.reads_attempted.append(name)
        return _RaisingQuery(self._factory)

    def rpc(self, name: str, *_a: Any, **_k: Any) -> _RaisingQuery:
        self.reads_attempted.append(name)
        return _RaisingQuery(self._factory)


# ══════════════════════════════════════════════════════════════════════════
# The endpoints, ENUMERATED from the application's own route table
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ReadEndpoint:
    """One Marketplace read endpoint, as the application itself declares it."""

    name: str
    method: str
    template: str
    path: str


def _enumerate_read_endpoints() -> Dict[str, ReadEndpoint]:
    """Every Marketplace read endpoint, from ``app.router.routes``.

    A read is a ``GET``. ``POST /compare`` reads too, but it is a POST with a body and belongs to
    the write-surface tests; ``GET`` is the exact set Requirement 6.7 calls "catalogue, search and
    detail", plus the caller-scoped and admin reads on the same router.
    """
    found: Dict[str, ReadEndpoint] = {}
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if getattr(route.endpoint, "__module__", "") != MARKETPLACE_ROUTER_MODULE:
            continue
        if "GET" not in route.methods:
            continue
        concrete = route.path
        for param in route.param_convertors:
            concrete = concrete.replace("{%s}" % param, PATH_PARAM_ID)
        found[route.endpoint.__name__] = ReadEndpoint(
            name=route.endpoint.__name__,
            method="GET",
            template=route.path,
            path=concrete,
        )
    return found


READ_ENDPOINTS: Dict[str, ReadEndpoint] = _enumerate_read_endpoints()


# ══════════════════════════════════════════════════════════════════════════
# The pending registry — known Requirement 1.5 defects, per clause
# ══════════════════════════════════════════════════════════════════════════

#: The clause names, used by :data:`PENDING` and by the ``xfail`` wiring below.
STATUS = "status"      # answers 500 or 503, and no numeric payload
CODE = "code"          # carries the stable MARKETPLACE_READ_FAILED code
LEAK = "leak"          # carries no internal
ENVELOPE = "envelope"  # is exactly {"error": {code, message, details}, "request_id"}


@dataclass(frozen=True)
class Pending:
    """A read endpoint that does not yet satisfy Requirement 1.5, and why."""

    clauses: FrozenSet[str]
    owner: str
    defect: str


#: Endpoint name -> the clauses it currently fails. Every entry is a DEFECT, not an exemption.
#: ``owner`` names the task that fixes it where ``tasks.md`` names one; "unowned" means the defect
#: was surfaced by this test and no task in the spec currently covers it.
#:
#: **EMPTY.** All twenty-three registered strict xfails across the two registries are closed:
#: ``creator_analytics`` and ``subscriber_analytics`` by task 21.1, the fourteen in
#: ``tests/test_subscriber_restricted_operations.py`` by task 17.9, and the last nine here by task
#: 21.3 — ``get_strategy_reviews``, ``get_categories``, ``check_deployment_permission_endpoint``,
#: ``my_library``, ``admin_pending_strategies``, ``get_subscription_status``,
#: ``admin_submission_detail``, ``admin_list_submissions`` and ``get_own_submission``. The header
#: records what each one used to answer and why each answer was a fabrication.
#:
#: The mechanism stays because it is how the next defect gets recorded — an entry here keeps
#: failing until its fix lands and turns into an ``XPASS`` failure the moment it passes, which is
#: the instruction to DELETE the entry. It is not a place to park an endpoint to silence it.
PENDING: Mapping[str, Pending] = {}


def _cases(clause: str):
    """``pytest.param`` per ``(endpoint, mode)``, ``xfail(strict=True)`` where ``PENDING`` says so.

    Strict, so a pending endpoint that starts passing fails this file rather than sliding by: the
    registry entry must then be deleted. Nothing is skipped — every case is executed.
    """
    params = []
    for name in sorted(READ_ENDPOINTS):
        pending = PENDING.get(name)
        for mode in sorted(FAILURE_MODES):
            marks = ()
            if pending is not None and clause in pending.clauses:
                marks = pytest.mark.xfail(
                    strict=True,
                    reason=(
                        f"{name} does not yet satisfy Requirement 1.5 ({clause}); "
                        f"owner: {pending.owner}. Defect: {pending.defect}"
                    ),
                )
            params.append(pytest.param(name, mode, marks=marks, id=f"{name}-{mode}"))
    return params


# ══════════════════════════════════════════════════════════════════════════
# Driving one endpoint against one failure mode (memoised)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Answer:
    """What one endpoint answered for one failure mode."""

    status_code: int
    text: str
    body: Any
    reads_attempted: Tuple[str, ...]


_ANSWERS: Dict[Tuple[str, str], Answer] = {}


async def _cache_miss(*_a: Any, **_k: Any) -> None:
    """Redis answering "not cached", so the route falls through to the read under test."""
    return None


async def _cache_write(*_a: Any, **_k: Any) -> None:
    return None


def _drive(endpoint: ReadEndpoint, mode: str) -> Answer:
    stub = _RaisingClient(FAILURE_MODES[mode])
    # The categories route memoises its answer in a module global; reset it or a previous test's
    # answer is served and the read under test never happens.
    library_router._CACHED_CATEGORIES = None

    app.dependency_overrides[get_current_user] = lambda: A_USER
    app.dependency_overrides[get_admin_user] = lambda: AN_ADMIN
    app.dependency_overrides[require_marketplace_access] = lambda: True
    try:
        with patch.object(
            library_router, "_build_service_client", return_value=stub
        ), patch.object(
            library_router, "_get_service_client", return_value=stub
        ), patch.object(
            library_router.redis_manager, "get", new=_cache_miss
        ), patch.object(
            library_router.redis_manager, "set", new=_cache_write
        ):
            response = client.request(endpoint.method, endpoint.path)
    finally:
        app.dependency_overrides.clear()

    try:
        body = response.json()
    except ValueError:  # pragma: no cover - a non-JSON body is itself a finding
        body = None
    return Answer(
        status_code=response.status_code,
        text=response.text,
        body=body,
        reads_attempted=tuple(stub.reads_attempted),
    )


def _answer(name: str, mode: str) -> Answer:
    """The memoised answer. See the module docstring on why each pair is fetched once."""
    key = (name, mode)
    if key not in _ANSWERS:
        _ANSWERS[key] = _drive(READ_ENDPOINTS[name], mode)
    answer = _ANSWERS[key]
    assert answer.status_code != 429, (
        f"{name} answered 429: this file exhausted the endpoint's rate-limit budget, so the "
        f"failure-mode answer was never observed"
    )
    return answer


# ══════════════════════════════════════════════════════════════════════════
# 0. The enumeration itself is not vacuous
# ══════════════════════════════════════════════════════════════════════════


def test_the_enumeration_found_the_marketplace_read_surface():
    """A guard on the guard. If the router module were renamed, the enumeration would silently
    yield nothing and every parametrised test below would vanish rather than fail."""
    assert READ_ENDPOINTS, (
        f"no GET route on {MARKETPLACE_ROUTER_MODULE} was found in app.router.routes; the "
        f"enumeration this file depends on is empty, so it asserts nothing"
    )
    for expected in ("browse_library", "get_library_detail", "get_creator_profile"):
        assert expected in READ_ENDPOINTS, (
            f"{expected} is not among the enumerated read endpoints "
            f"({sorted(READ_ENDPOINTS)}); the enumeration is not seeing the catalogue reads"
        )


def test_the_pending_registry_names_only_real_endpoints():
    """An entry for an endpoint that no longer exists would sit here forever, exempting nothing
    and hiding the fact that the defect is gone."""
    unknown = sorted(set(PENDING) - set(READ_ENDPOINTS))
    assert not unknown, (
        f"PENDING names endpoints that are not in the enumerated read surface: {unknown}. "
        f"Delete the stale entries."
    )
    for name, pending in PENDING.items():
        assert pending.clauses <= {STATUS, CODE, LEAK, ENVELOPE}, (
            f"PENDING[{name!r}] names a clause this file does not check: "
            f"{sorted(pending.clauses)}"
        )


#: Spelled-out counts, for the docstring check below. Only the range this file can reach.
_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty"
).split()


def test_this_files_own_docstring_states_the_counts_it_actually_has():
    """The registry shrinks as tasks land, and prose does not shrink with it.

    This file's header told readers "eleven of the enumerated read endpoints" for as long as it
    took somebody to notice that task 21.1 had taken the count to nine — a sentence describing
    deleted state as live. The count is derivable, so it is asserted rather than trusted: change
    the registry or add a read endpoint and this fails until the header says what is true.
    """
    # Whitespace-normalised, so the check is about the sentence rather than about where the
    # 100-column rewrap happened to break it.
    doc = re.sub(r"\s+", " ", __doc__ or "")
    pending = len(PENDING)
    total = len(READ_ENDPOINTS)
    correct = total - pending
    assert pending < len(_NUMBER_WORDS) and total < len(_NUMBER_WORDS), (
        f"{pending} pending of {total} endpoints is outside the range this check spells out; "
        f"extend _NUMBER_WORDS"
    )

    expected = (
        f"{_NUMBER_WORDS[pending].capitalize()} of the {_NUMBER_WORDS[total]} enumerated read "
        f"endpoints"
    )
    assert expected in doc, (
        f"this file's docstring does not say {expected!r}. {pending} of the {total} enumerated "
        f"read endpoints are in PENDING; the header has to say so."
    )
    expected_correct = f"the {_NUMBER_WORDS[correct]} that are already correct"
    assert expected_correct in doc, (
        f"this file's docstring does not say {expected_correct!r}. {correct} of the {total} "
        f"enumerated read endpoints are asserted strictly."
    )


@pytest.mark.parametrize("mode", sorted(FAILURE_MODES))
@pytest.mark.parametrize("endpoint", sorted(READ_ENDPOINTS))
def test_the_failure_mode_actually_reached_the_persistence_layer(endpoint, mode):
    """Every case below is only meaningful if the endpoint really attempted a read against the
    failing client. An endpoint that answered without touching it (an early return, a cache hit, a
    guard in front of the read) would pass or fail for reasons that have nothing to do with
    Requirement 1.5, so that is asserted explicitly rather than assumed."""
    answer = _answer(endpoint, mode)
    assert answer.reads_attempted, (
        f"{endpoint} answered {answer.status_code} on a {mode} without attempting a single read "
        f"against the failing client, so this file cannot claim to cover it. Body: "
        f"{answer.text[:400]!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. No read failure becomes a success body (Requirements 1.5, 1.7)
# ══════════════════════════════════════════════════════════════════════════

#: Keys that name a FIGURE. Their presence in a read-failure body is a fabrication whatever the
#: value: ``total: 0`` invents a count, and ``total: null`` invents the claim that a count was
#: read and was empty (Requirement 28.5's omitted-vs-null distinction).
FIGURE_KEYS: FrozenSet[str] = frozenset(
    {
        "total",
        "count",
        "total_earnings_usd",
        "monthly_recurring_revenue",
        "platform_fee_paid",
        "active_subscribers",
        "published_strategies_count",
        "rating_average",
        "avg_rating",
        "rating_count",
        "monthly_spend_usd",
        "active_subscriptions_count",
        "total_subscriptions_count",
        "subscriber_count",
        "clone_count",
        "mrr",
        "price",
        "price_minor",
    }
)

#: The only key a read-failure body may carry a number under: the legacy error envelope's echo of
#: the HTTP status it is already sending. It is not a figure and it is not derived from a read.
PERMITTED_NUMERIC_KEYS: FrozenSet[str] = frozenset({"status_code"})


def _numbers_in(body: Any, path: str = "", key: Optional[str] = None) -> List[str]:
    """Every numeric leaf in ``body``, as ``"path=value"``, excluding the permitted keys.

    ``bool`` is not counted: ``True``/``False`` is a flag, not a figure, and Python makes it an
    ``int`` subclass.
    """
    found: List[str] = []
    if isinstance(body, dict):
        for k, value in body.items():
            found.extend(_numbers_in(value, f"{path}.{k}", str(k)))
    elif isinstance(body, list):
        for index, value in enumerate(body):
            found.extend(_numbers_in(value, f"{path}[{index}]", key))
    elif isinstance(body, bool):
        pass
    elif isinstance(body, (int, float)):
        if key not in PERMITTED_NUMERIC_KEYS:
            found.append(f"{path or '<root>'}={body!r}")
    return found


def _figure_keys_in(body: Any, path: str = "") -> List[str]:
    """Every FIGURE_KEYS key present anywhere in ``body``, whatever its value."""
    found: List[str] = []
    if isinstance(body, dict):
        for key, value in body.items():
            if key in FIGURE_KEYS:
                found.append(f"{path}.{key}={value!r}")
            found.extend(_figure_keys_in(value, f"{path}.{key}"))
    elif isinstance(body, list):
        for index, value in enumerate(body):
            found.extend(_figure_keys_in(value, f"{path}[{index}]"))
    return found


@pytest.mark.parametrize("endpoint,mode", _cases(STATUS))
def test_read_failure_answers_500_or_503_with_no_numeric_payload(endpoint, mode):
    """Requirement 1.5's status, and Requirement 1.7's "no fabricated figure", together.

    Together because the pair is the actual defect: an endpoint that answers 200 *and* fills the
    figures is what shows a creator ``$0.00`` earnings for a query that never ran.
    """
    answer = _answer(endpoint, mode)

    assert answer.status_code in (500, 503), (
        f"{endpoint} answered {answer.status_code} on a {mode}. A Marketplace read that failed "
        f"must answer 500 or 503 (Requirement 1.5) — never a success, and never a 404 that "
        f"reports the caller's data as absent. Body: {answer.text[:400]!r}"
    )

    fabricated = _figure_keys_in(answer.body)
    assert not fabricated, (
        f"{endpoint} on a {mode} returned a body carrying fabricated figures {fabricated}; "
        f"Requirement 1.7 forbids any numeric field that was not read. Body: "
        f"{answer.text[:400]!r}"
    )

    numbers = _numbers_in(answer.body)
    assert not numbers, (
        f"{endpoint} on a {mode} returned a body carrying the numeric payload {numbers}; a read "
        f"failure carries a code and a sentence, not figures. Body: {answer.text[:400]!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. The code is the stable catalogue code (Requirement 1.5)
# ══════════════════════════════════════════════════════════════════════════


def _code_of(body: Any) -> str:
    """The stable error code a structured body carries, or ``""`` when there is none.

    A ``MarketplaceError`` answers ``{"error": {"code": …}, …}``. The legacy envelope puts a
    non-catalogue string in ``error`` and an ``HTTPException`` puts a sentence in ``detail``;
    both read as "no stable code", which is the point.
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return ""


@pytest.mark.parametrize("endpoint,mode", _cases(CODE))
def test_read_failure_carries_the_stable_marketplace_read_failed_code(endpoint, mode):
    """Requirement 1.5's "stable machine-readable error code". One code for every read failure on
    every endpoint, so a client can branch on it rather than on a sentence or a status alone."""
    answer = _answer(endpoint, mode)
    code = _code_of(answer.body)

    assert code == errors.MARKETPLACE_READ_FAILED, (
        f"{endpoint} on a {mode} answered {answer.status_code} carrying code {code!r}, not the "
        f"stable {errors.MARKETPLACE_READ_FAILED!r}. Body: {answer.text[:400]!r}"
    )
    assert answer.status_code in errors.ALLOWED_HTTP_STATUS_FOR_CODE[
        errors.MARKETPLACE_READ_FAILED
    ], (
        f"{endpoint} on a {mode} carried {code!r} with status {answer.status_code}, which the "
        f"catalogue does not permit for that code"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. No error body carries an internal (Requirement 22.9)
# ══════════════════════════════════════════════════════════════════════════

#: A dashed UUID anywhere in a body. The request identifier is an undashed hex string, so it is
#: not matched here; the caller's own identifiers are allow-listed explicitly.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: Shapes a stack trace, a filesystem path or a Python frame takes. Complements
#: ``errors.FORBIDDEN_BODY_SUBSTRINGS`` (the module's own deny-list, reused rather than copied)
#: with the ones only a real traceback carries.
TRACE_AND_PATH_MARKERS: Tuple[str, ...] = (
    "traceback",
    "most recent call last",
    ".py",
    "site-packages",
    'file "',
    ", line ",
    "site-packages",
    "/lib/",
    "\\lib\\",
)


def _strings_in(body: Any, path: str = "") -> List[Tuple[str, str]]:
    """Every string leaf in ``body``, with the path it sits at. Keys are included: a key is as
    visible to a client as a value, and a leaked column name would arrive as one."""
    found: List[Tuple[str, str]] = []
    if isinstance(body, dict):
        for key, value in body.items():
            found.append((f"{path}.<key>", str(key)))
            found.extend(_strings_in(value, f"{path}.{key}"))
    elif isinstance(body, list):
        for index, value in enumerate(body):
            found.extend(_strings_in(value, f"{path}[{index}]"))
    elif isinstance(body, str):
        found.append((path or "<root>", body))
    return found


def _assert_carries_no_internals(body: Any, text: str, *, what: str) -> None:
    """Requirement 22.9 in full, on one body.

    Five things, none of which a client may receive: a stack trace, a database error string,
    query text, an internal filesystem path, and an internal identifier that is not the caller's
    own. ``errors.FORBIDDEN_BODY_SUBSTRINGS`` supplies the first four's deny-list — the module's
    own definition, so the response surface and the scrubber cannot disagree about what "internal"
    means (Requirement 30.2).
    """
    lowered = text.lower()

    for marker in errors.FORBIDDEN_BODY_SUBSTRINGS:
        assert marker.lower() not in lowered, (
            f"{what} carries the deny-listed internal {marker!r} "
            f"(errors.FORBIDDEN_BODY_SUBSTRINGS): {text[:400]!r}"
        )
    for marker in DRIVER_INTERNALS:
        assert marker.lower() not in lowered, (
            f"{what} echoes the driver's own {marker!r} — a database error string or query text "
            f"reached the client: {text[:400]!r}"
        )
    for marker in TRACE_AND_PATH_MARKERS:
        assert marker.lower() not in lowered, (
            f"{what} carries {marker!r}, the shape of a stack trace or an internal filesystem "
            f"path: {text[:400]!r}"
        )

    permitted_ids = set(CALLER_OWN_IDENTIFIERS)
    if isinstance(body, dict) and isinstance(body.get("request_id"), str):
        permitted_ids.add(body["request_id"])

    for where, value in _strings_in(body):
        for found in _UUID_RE.findall(value):
            assert found in permitted_ids, (
                f"{what} carries the identifier {found!r} at {where}, which is neither the "
                f"caller's own nor a resource the caller named: {text[:400]!r}"
            )


@pytest.mark.parametrize("endpoint,mode", _cases(LEAK))
def test_error_body_carries_no_stack_trace_query_path_or_foreign_identifier(endpoint, mode):
    """Requirement 22.9 on the live bodies. The four failure shapes each carry a sqlstate, a
    column name, query text or an internal host into the handler; none of it may come back out."""
    answer = _answer(endpoint, mode)
    _assert_carries_no_internals(
        answer.body,
        answer.text,
        what=f"{endpoint}'s {answer.status_code} body on a {mode}",
    )


#: A ``details`` payload poisoned with every shape Requirement 22.9 forbids, as a careless raiser
#: would pass one: the driver's message, the query, a traceback, an absolute path and another
#: user's identifier.
POISONED_DETAILS: Dict[str, Any] = {
    "driver": "psycopg2.errors.UndefinedColumn: column library_strategies.monthly_price",
    "query": "SELECT id, price_minor FROM library_strategies WHERE author_id = $1",
    "trace": 'Traceback (most recent call last):\n  File "backend_app/routers/library.py"',
    "path": "C:\\aerora_quant_backend\\backend_app\\routers\\library.py",
    "sqlstate": "23505 on uq_submission_open_per_strategy",
    "nested": {"deeper": ["strategy_backtests", "/usr/lib/python3/site-packages/psycopg"]},
}


@pytest.mark.parametrize("code", sorted(errors.ERROR_CODES))
def test_the_scrubber_not_the_raiser_keeps_internals_out_of_the_body(code):
    """The guarantee must not depend on every raiser remembering it.

    A ``details`` mapping poisoned with a driver message, query text, a traceback, an absolute
    path and a foreign identifier is handed to the catalogue for EVERY code; nothing forbidden may
    survive into the body. This is what makes Requirement 22.9 a property of ``errors.py`` rather
    than of each call site's discipline.
    """
    error = errors.StructuredError(code, details=POISONED_DETAILS)
    body = errors.structured_error_body(error, "req-abc")
    text = repr(body)

    for marker in ("monthly_price", "SELECT", "Traceback", "psycopg", "23505", "library.py"):
        assert marker.lower() not in text.lower(), (
            f"{code}'s body let the poisoned {marker!r} through; redact_details did not scrub it: "
            f"{text[:400]}"
        )
    # Redacted, not dropped: a caller must be able to tell "withheld" from "not sent".
    assert set(body["error"]["details"]) == set(POISONED_DETAILS), (
        f"{code}'s body dropped details keys instead of redacting their values; an omitted key "
        f"and a redacted key mean different things: {body['error']['details']}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. The envelope shape, and the stability of the codes
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("endpoint,mode", _cases(ENVELOPE))
def test_read_failure_body_is_exactly_the_declared_envelope(endpoint, mode):
    """``{"error": {"code", "message", "details"}, "request_id"}`` — that shape and no other.

    The key sets come from ``errors.ERROR_ENVELOPE_KEYS``/``ERROR_OBJECT_KEYS``, so this test and
    ``structured_error_body`` cannot drift apart. An extra member is not cosmetic: the legacy
    envelope's ``path`` is an internal route Requirement 22.9 excludes, and its ``timestamp``
    duplicates what the log record already holds under the same request identifier.
    """
    answer = _answer(endpoint, mode)
    body = answer.body

    assert isinstance(body, dict), (
        f"{endpoint} on a {mode} did not answer a JSON object: {answer.text[:400]!r}"
    )
    assert set(body) == set(errors.ERROR_ENVELOPE_KEYS), (
        f"{endpoint} on a {mode} answered with keys {sorted(body)}; the declared envelope is "
        f"{sorted(errors.ERROR_ENVELOPE_KEYS)}. Body: {answer.text[:400]!r}"
    )
    assert isinstance(body["error"], dict), (
        f"{endpoint} on a {mode} put {body['error']!r} in \"error\" rather than the error object"
    )
    assert set(body["error"]) == set(errors.ERROR_OBJECT_KEYS), (
        f"{endpoint} on a {mode} answered an error object with keys {sorted(body['error'])}; "
        f"the declared shape is {sorted(errors.ERROR_OBJECT_KEYS)}"
    )
    assert isinstance(body["request_id"], str) and body["request_id"], (
        f"{endpoint} on a {mode} answered without a request identifier (Requirement 26.1)"
    )
    assert errors.is_known_code(body["error"]["code"]), (
        f"{endpoint} on a {mode} answered the code {body['error']['code']!r}, which is not in "
        f"the declared catalogue"
    )
    assert body["error"]["message"] == errors.message_for_code(body["error"]["code"]), (
        f"{endpoint} on a {mode} answered a message that is not the catalogue's sentence for "
        f"{body['error']['code']!r}; PUBLIC_MESSAGE_FOR_CODE is the single place one is written"
    )


@pytest.mark.parametrize("code", sorted(errors.ERROR_CODES))
def test_every_catalogue_code_serialises_to_the_declared_envelope(code):
    """The shape holds for every code, not only for the read failure the endpoints above raise."""
    body = errors.structured_error_body(errors.StructuredError(code), "req-abc")
    assert set(body) == set(errors.ERROR_ENVELOPE_KEYS)
    assert set(body["error"]) == set(errors.ERROR_OBJECT_KEYS)
    assert body["error"]["code"] == code
    assert body["request_id"] == "req-abc"


def test_a_code_outside_the_catalogue_cannot_be_raised():
    """Why the codes are stable: the constructor is the gate, not a convention.

    This is what makes the two dynamic code sources safe — a mapping that produced an unknown
    string would raise here rather than putting an ad-hoc code on the wire.
    """
    with pytest.raises(ValueError):
        errors.MarketplaceError("MARKETPLACE_SOMETHING_INVENTED_AT_A_CALL_SITE")
    with pytest.raises(ValueError):
        errors.MarketplaceError(errors.PAPER_ORDER_INVALID)  # right catalogue, wrong surface
    with pytest.raises(ValueError):
        # A read failure cannot be answered 200 or 404 even by an explicit override.
        errors.MarketplaceError(errors.MARKETPLACE_READ_FAILED, http_status=404)


def test_every_dynamic_code_source_resolves_into_the_catalogue():
    """The two places a code is chosen at runtime rather than named in the source."""
    for service_code, catalogue_code in library_router._SERVICE_CODE_TO_CATALOGUE.items():
        assert catalogue_code in errors.ERROR_CODES, (
            f"_SERVICE_CODE_TO_CATALOGUE maps {service_code!r} onto {catalogue_code!r}, which is "
            f"not a catalogue code"
        )
    for reason, wire_code in _entitlement_resolver.WIRE_CODE_FOR_REASON.items():
        assert wire_code is None or wire_code in errors.ERROR_CODES, (
            f"WIRE_CODE_FOR_REASON maps {reason!r} onto {wire_code!r}, which is not a catalogue "
            f"code"
        )


#: The modules whose ``MarketplaceError`` call sites are scanned for an ad-hoc code.
_SCANNED_MODULES = ("backend_app/routers/library.py",)
_ERROR_CLASSES = ("MarketplaceError", "PaperError", "StructuredError")


def _statically_named_codes(source_path: str) -> List[Tuple[int, str]]:
    """Every ``(line, identifier)`` a structured error is constructed with, where the argument is
    written in the source rather than computed. A literal is reported as its own text."""
    tree = ast.parse(io.open(source_path, encoding="utf-8").read(), source_path)
    named: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in _ERROR_CLASSES or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            named.append((node.lineno, argument.value))
        elif isinstance(argument, ast.Name):
            named.append((node.lineno, argument.id))
        elif isinstance(argument, ast.Attribute):
            named.append((node.lineno, argument.attr))
    return named


def test_no_call_site_names_a_code_outside_the_catalogue():
    """Requirement: the codes are drawn from the declared catalogue, not assembled at a call site.

    Every statically written code argument — a string literal, or the identifier of a catalogue
    constant — must resolve to a member of ``errors.ERROR_CODES``. A computed argument is not
    inspected here; it is gated at construction, which
    :func:`test_a_code_outside_the_catalogue_cannot_be_raised` proves.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    checked = 0
    for relative in _SCANNED_MODULES:
        source_path = os.path.join(root, relative)
        for line, identifier in _statically_named_codes(source_path):
            resolved = getattr(errors, identifier, identifier)
            if identifier not in dir(errors) and identifier not in errors.ERROR_CODES:
                # A local variable holding a computed code (e.g. a mapping lookup). Gated by the
                # constructor, and its source mapping is checked above.
                continue
            checked += 1
            assert resolved in errors.ERROR_CODES, (
                f"{relative}:{line} raises a structured error with {identifier!r} -> "
                f"{resolved!r}, which is not in the declared catalogue"
            )
    assert checked, (
        "no statically named error code was found in the scanned modules; the scan is not "
        "reaching the call sites it is meant to check"
    )


# ══════════════════════════════════════════════════════════════════════════
# The public messages carry no internal (Requirements 2.10, 4.8, 22.9)
# ══════════════════════════════════════════════════════════════════════════


def _digit_sequences_from(*mappings) -> List[str]:
    """Every distinct digit spelling of every value in the given numeric mappings.

    ``evidence_validator.THRESHOLDS`` and ``pricing_evaluator.WEIGHTS`` are the internal numbers
    Requirements 2.10 and 22.9 forbid a public message from carrying. Their ``str`` forms are what
    a leaked message would spell, so those are what the deny-list is fed.
    """
    sequences = set()
    for mapping in mappings:
        for value in mapping.values():
            sequences.add(str(value))
    return sorted(sequences)


THRESHOLD_AND_WEIGHT_DIGITS = _digit_sequences_from(
    evidence_validator.THRESHOLDS, pricing_evaluator.WEIGHTS
)


def test_public_messages_carry_no_internal_substring_or_digit():
    """Delegates to the one enforcement point, fed the live threshold and weight digits."""
    errors.assert_messages_carry_no_internals(
        forbidden_digit_sequences=THRESHOLD_AND_WEIGHT_DIGITS
    )


def test_public_messages_carry_no_deny_listed_substring_explicitly():
    """The exact substrings task 16.5 names, checked one message at a time so a failure says which
    code and which substring, independent of the helper above."""
    named = (
        "select",
        "library_strategies",
        "strategy_backtests",
        "traceback",
        "psycopg",
        "42703",
        "23505",
    )
    for code, message in errors.PUBLIC_MESSAGE_FOR_CODE.items():
        lowered = message.lower()
        for substring in named:
            assert substring not in lowered, (
                f"{code}'s public message contains the forbidden substring {substring!r}: "
                f"{message!r}"
            )


def test_public_messages_carry_no_threshold_or_weight_digit():
    """Requirement 2.10's exclusion list, concretely: an eligibility rejection must not disclose
    the internal thresholds it was judged against."""
    for code, message in errors.PUBLIC_MESSAGE_FOR_CODE.items():
        for sequence in THRESHOLD_AND_WEIGHT_DIGITS:
            assert sequence not in message, (
                f"{code}'s public message contains the internal value {sequence!r} from "
                f"THRESHOLDS/WEIGHTS: {message!r}"
            )


def test_public_messages_carry_no_digit_at_all():
    """The rule the catalogue's docstring states: numeric detail lives in ``details``, never in the
    sentence. Subsumes the threshold check and is the durable guard under future edits."""
    digit = re.compile(r"\d")
    for code, message in errors.PUBLIC_MESSAGE_FOR_CODE.items():
        found = digit.search(message)
        assert found is None, (
            f"{code}'s public message contains the digit {found.group()!r}; numeric detail "
            f"belongs in details, not the sentence: {message!r}"
        )


def test_threshold_and_weight_digits_were_actually_collected():
    """A guard on the guard: if THRESHOLDS/WEIGHTS ever became empty, the deny-list would pass
    vacuously."""
    assert THRESHOLD_AND_WEIGHT_DIGITS, (
        "no digit sequences were collected from THRESHOLDS/WEIGHTS; the internal-value check "
        "would be vacuous"
    )
    assert all(
        sequence.lstrip("-").replace(".", "", 1).isdigit()
        for sequence in THRESHOLD_AND_WEIGHT_DIGITS
    ), f"expected only numeric spellings; got {THRESHOLD_AND_WEIGHT_DIGITS}"


def test_every_catalogue_code_has_exactly_one_public_message_and_one_status():
    """No code may reach a client without a declared sentence and a declared status; a gap would
    otherwise surface as a ``KeyError`` inside the handler, at the worst possible moment."""
    assert set(errors.PUBLIC_MESSAGE_FOR_CODE) == set(errors.ERROR_CODES), (
        "PUBLIC_MESSAGE_FOR_CODE and ERROR_CODES disagree: "
        f"missing messages {sorted(set(errors.ERROR_CODES) - set(errors.PUBLIC_MESSAGE_FOR_CODE))}, "
        f"orphan messages {sorted(set(errors.PUBLIC_MESSAGE_FOR_CODE) - set(errors.ERROR_CODES))}"
    )
    assert set(errors.HTTP_STATUS_FOR_CODE) == set(errors.ERROR_CODES)


# ══════════════════════════════════════════════════════════════════════════
# 5. A read that COMPLETED still answers exactly what it answered before
#    (Requirement 30.5, and Requirement 21.4 for the two not-found shapes)
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THIS SECTION EXISTS
# -----------------------
# Task 21.3 changed nine LIVE read endpoints. Every change is inside an `except` block or a
# `data is None` guard, so in principle no success path moved — but "in principle" is exactly the
# claim a test is for. The four sections above would all still pass if a fix had turned a
# legitimately empty result into a 503, or an ownership grant into a refusal, because they only
# ever drive the endpoints against a client that FAILS.
#
# So each of the nine is driven a second time against a client whose reads SUCCEED, and its
# `(status, body)` is pinned literally below. Nothing is derived from the handler: the expected
# bodies are written out, so a change to a projection, a key name, a default or a message is a
# failure here rather than a silent contract change.
#
# THREE CASES ARE PINNED TWICE, AND THOSE ARE THE IMPORTANT ONES
#   `get_subscription_status`, `check_deployment_permission_endpoint` and `get_own_submission`
#   each have a "read completed and matched nothing" case as well as a "read returned rows" one.
#   That empty result is precisely what the deleted `except` blocks were indistinguishable from,
#   so it is where a careless fix would do its damage:
#
#     * `get_subscription_status` must still answer 200 `not_subscribed` — that zero was measured;
#     * `check_deployment_permission_endpoint` must still answer a 200 DENIAL — the endpoint
#       refusing an UNREADABLE check must not have made it refuse to answer a readable one, and it
#       certainly must not have become an admission (`has_permission: true`); and
#     * `get_own_submission` must still answer 404 `MARKETPLACE_SUBMISSION_NOT_FOUND` — Requirement
#       21.4's indistinguishability between "absent" and "another owner's" depends on that 404
#       surviving, and converting a genuine cross-tenant 404 into a 503 would break it.


class _SucceedingQuery:
    """A fluent builder whose ``.execute()`` SUCCEEDS, answering the scripted rows for its table.

    The mirror image of :class:`_RaisingQuery`: every verb resolves to a ``_Verb`` so an arbitrary
    chain builds, and only the terminal ``.execute()`` does anything. Column lists, filters,
    ordering and ranges are not simulated — this section pins what a handler DOES with rows that
    arrived, and the filters are the Persistence_Layer's business, asserted where they are the
    subject (``tests/test_library_detail_visibility_and_omission.py``,
    ``tests/test_admin_review_surface.py``).

    ``.single()`` is the one verb with behaviour, because ``_get_author_alias`` reads
    ``resp.data.get("display_name")`` from it: a single-row read answers the first row as a MAPPING
    rather than a list, exactly as PostgREST does.
    """

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self._single = False

    def __getattr__(self, _name: str) -> _Verb:
        return _Verb(self)

    def single(self) -> "_SucceedingQuery":
        self._single = True
        return self

    def execute(self) -> "_Response":
        if self._single:
            return _Response(dict(self._rows[0]) if self._rows else None, len(self._rows))
        return _Response([dict(row) for row in self._rows], len(self._rows))


class _Response:
    """A PostgREST-shaped response: ``.data``, ``.count`` and no ``error``.

    ``count`` is carried because ``admin_list_submissions`` asks for ``count="exact"`` and returns
    ``resp.count`` as its ``total`` — a figure that must come from the read, never from
    ``len(items)`` on a page.
    """

    error = None

    def __init__(self, data: Any, count: int) -> None:
        self.data = data
        self.count = count


class _SucceedingClient:
    """A service-role client that answers each table with its scripted rows.

    A read of a table the case did not script is an ``AssertionError``, not an empty list: an
    unscripted read means the handler is doing something this case did not anticipate, and
    answering it with ``[]`` would let that pass as a success.
    """

    def __init__(self, rows_by_table: Mapping[str, List[Dict[str, Any]]]) -> None:
        self._rows_by_table = rows_by_table
        self.reads_attempted: List[str] = []

    def _query(self, name: str) -> _SucceedingQuery:
        self.reads_attempted.append(name)
        assert name in self._rows_by_table, (
            f"the handler read {name!r}, which this success case did not script "
            f"(scripted: {sorted(self._rows_by_table)}). Script it and pin what it answers."
        )
        return _SucceedingQuery(self._rows_by_table[name])

    def table(self, name: str) -> _SucceedingQuery:
        return self._query(name)

    def from_(self, name: str) -> _SucceedingQuery:
        return self._query(name)

    def rpc(self, name: str, *_a: Any, **_k: Any) -> _SucceedingQuery:
        return self._query(name)


#: The identifiers the scripted rows carry. Distinct from :data:`PATH_PARAM_ID` where the value is
#: the server's rather than the caller's, so a body echoing the wrong one is visible.
A_LISTING_ID = "11111111-1111-1111-1111-111111111111"
A_SUBSCRIPTION_ID = "22222222-2222-2222-2222-222222222222"
A_REVIEWER_ID = "33333333-3333-3333-3333-333333333333"

#: One ``marketplace_submissions`` row, in the shape both submission reads select. Shared by
#: ``admin_list_submissions``, ``get_own_submission`` and ``admin_submission_detail`` so the three
#: are pinned against the same row and a divergence between them is visible.
A_SUBMISSION_ROW: Dict[str, Any] = {
    "id": PATH_PARAM_ID,
    "listing_id": A_LISTING_ID,
    "source_strategy_id": A_LISTING_ID,
    "owner_id": A_USER["id"],
    "version_id": A_LISTING_ID,
    "submission_state": "SUBMITTED",
    "eligibility_outcomes": [],
    "evaluator_version": "v1",
    "rejection_reason": None,
    "reviewed_by": None,
    "submitted_at": "2024-03-01T00:00:00+00:00",
    "reviewed_at": None,
    "published_at": None,
    "created_at": "2024-03-01T00:00:00+00:00",
    "updated_at": "2024-03-01T00:00:00+00:00",
}

#: The admin list selects a narrower column set than the owner read; the fake does not simulate
#: column projection, so the list case scripts the row the narrower select would produce. Written
#: out rather than derived, so the two column lists cannot silently converge.
A_LISTED_SUBMISSION_ROW: Dict[str, Any] = {
    key: value
    for key, value in A_SUBMISSION_ROW.items()
    if key not in ("eligibility_outcomes", "rejection_reason", "reviewed_by")
}

AN_EVIDENCE_ROW: Dict[str, Any] = {
    "id": A_LISTING_ID,
    "condition_index": 0,
    "symbol": "BTCUSDT",
}

A_TRANSITION_ROW: Dict[str, Any] = {
    "id": A_LISTING_ID,
    "from_state": None,
    "to_state": "SUBMITTED",
    "actor_id": A_USER["id"],
    "reason": None,
    "transitioned_at": "2024-03-01T00:00:00+00:00",
}


@dataclass(frozen=True)
class SuccessCase:
    """One endpoint, one scripted successful read, and the answer it must still give.

    Either ``body`` (the exact JSON object) or ``code`` (the catalogue code of an answer that is
    legitimately an error, such as the 404 for a Submission that is not the caller's) is pinned —
    never neither, which :func:`test_every_success_case_pins_an_answer` asserts.
    """

    endpoint: str
    label: str
    rows: Mapping[str, List[Dict[str, Any]]]
    status_code: int
    body: Optional[Dict[str, Any]] = None
    code: Optional[str] = None
    tables_read: Tuple[str, ...] = ()


#: The nine endpoints task 21.3 changed, and nothing else: this section is a revert-detector for
#: that change, not a second functional suite for the whole router.
SUCCESS_CASES: Tuple[SuccessCase, ...] = (
    SuccessCase(
        endpoint="get_categories",
        label="rows",
        rows={
            "library_strategies": [
                {"category": "trend"},
                {"category": "trend"},
                {"category": "mean_reversion"},
                # A null category is counted for no bucket, exactly as before.
                {"category": None},
            ]
        },
        status_code=200,
        body={
            "categories": [
                {"name": "trend", "count": 2},
                {"name": "mean_reversion", "count": 1},
            ]
        },
        tables_read=("library_strategies",),
    ),
    SuccessCase(
        endpoint="my_library",
        label="rows",
        rows={
            "library_strategies": [
                {
                    "id": A_LISTING_ID,
                    "name": "Momentum",
                    "moderation_status": "approved",
                    "clone_count": 3,
                    "avg_rating": 4.5,
                    "rating_count": 2,
                    "is_active": True,
                    "published_at": "2024-01-01T00:00:00+00:00",
                    # Not in _MY_LIBRARY_FIELDS. The entry is BUILT from the frozen tuple, so a
                    # column a migration adds cannot ride along into the owner's response.
                    "buy_logic": "PROTECTED",
                }
            ]
        },
        status_code=200,
        body={
            "items": [
                {
                    "id": A_LISTING_ID,
                    "name": "Momentum",
                    "moderation_status": "approved",
                    "clone_count": 3,
                    "avg_rating": 4.5,
                    "rating_count": 2,
                    "is_active": True,
                    "published_at": "2024-01-01T00:00:00+00:00",
                }
            ],
            "total": 1,
        },
        tables_read=("library_strategies",),
    ),
    SuccessCase(
        endpoint="admin_pending_strategies",
        label="rows",
        rows={
            "library_strategies": [
                {
                    "id": A_LISTING_ID,
                    "name": "Awaiting review",
                    "author_id": A_USER["id"],
                    "category": "trend",
                    "difficulty": "intermediate",
                    "moderation_status": "pending",
                    "published_at": "2024-01-01T00:00:00+00:00",
                    "clone_count": 0,
                    "is_featured": False,
                }
            ],
            # The ONE batched alias read for the whole queue (Requirement 27.1).
            "profiles": [{"id": A_USER["id"], "display_name": "Quant Jane"}],
        },
        status_code=200,
        body={
            "items": [
                {
                    "id": A_LISTING_ID,
                    "name": "Awaiting review",
                    "author_id": A_USER["id"],
                    "category": "trend",
                    "difficulty": "intermediate",
                    "moderation_status": "pending",
                    "published_at": "2024-01-01T00:00:00+00:00",
                    "clone_count": 0,
                    "is_featured": False,
                    "author_alias": "Quant Jane",
                }
            ],
            "total": 1,
        },
        tables_read=("library_strategies", "profiles"),
    ),
    SuccessCase(
        endpoint="get_strategy_reviews",
        label="rows",
        rows={
            "library_ratings": [
                {
                    "rating": 5,
                    "review_text": "Solid",
                    "created_at": "2024-02-01T00:00:00+00:00",
                    "user_id": A_REVIEWER_ID,
                }
            ],
            "profiles": [{"id": A_REVIEWER_ID, "display_name": "Reader Ray"}],
        },
        status_code=200,
        body={
            "reviews": [
                {
                    "rating": 5,
                    "review_text": "Solid",
                    "created_at": "2024-02-01T00:00:00+00:00",
                    # The reviewer's identifier is REPLACED by the alias, never carried alongside.
                    "user_alias": "Reader Ray",
                }
            ],
            "total": 1,
        },
        tables_read=("library_ratings", "profiles"),
    ),
    SuccessCase(
        endpoint="get_subscription_status",
        label="subscribed",
        rows={
            "library_subscriptions": [
                {
                    "id": A_SUBSCRIPTION_ID,
                    "status": "active",
                    "started_at": "2024-01-01T00:00:00+00:00",
                    "expires_at": "2024-02-01T00:00:00+00:00",
                }
            ]
        },
        status_code=200,
        body={
            "status": "active",
            "subscription_id": A_SUBSCRIPTION_ID,
            "library_id": PATH_PARAM_ID,
            "started_at": "2024-01-01T00:00:00+00:00",
            "expires_at": "2024-02-01T00:00:00+00:00",
            "message": "Subscription status: active",
        },
        tables_read=("library_subscriptions",),
    ),
    SuccessCase(
        # The measured zero. A read that COMPLETED and matched no row is an ANSWER, and it must
        # still be answered — this is the case a careless read-failure fix turns into a 503.
        endpoint="get_subscription_status",
        label="read_completed_no_subscription",
        rows={"library_subscriptions": []},
        status_code=200,
        body={
            "status": "not_subscribed",
            "library_id": PATH_PARAM_ID,
            "message": (
                "No subscription found. Complete checkout at "
                "/api/library/{library_id}/checkout"
            ),
        },
        tables_read=("library_subscriptions",),
    ),
    SuccessCase(
        endpoint="check_deployment_permission_endpoint",
        label="owner",
        rows={"library_strategies": [{"author_id": A_USER["id"]}]},
        status_code=200,
        body={
            "library_id": PATH_PARAM_ID,
            "user_id": A_USER["id"],
            "has_permission": True,
            "granted_via": "ownership",
            "subscription_id": None,
            "expires_at": None,
        },
        # The subscription read is not reached: ownership answered.
        tables_read=("library_strategies",),
    ),
    SuccessCase(
        # BOTH reads completed and found nothing. That is a real denial and it must still be
        # answered as one — the fix refuses an UNREADABLE check, not a readable refusal. And it
        # must not have become `has_permission: true`: a read failure may never turn into an
        # admission.
        endpoint="check_deployment_permission_endpoint",
        label="read_completed_no_entitlement",
        rows={"library_strategies": [], "library_subscriptions": []},
        status_code=200,
        body={
            "library_id": PATH_PARAM_ID,
            "user_id": A_USER["id"],
            "has_permission": False,
            "reason": "No valid subscription or ownership",
        },
        tables_read=("library_strategies", "library_subscriptions"),
    ),
    SuccessCase(
        endpoint="admin_list_submissions",
        label="rows",
        rows={"marketplace_submissions": [A_LISTED_SUBMISSION_ROW]},
        status_code=200,
        body={
            "items": [A_LISTED_SUBMISSION_ROW],
            # From the read's own `count="exact"`, not from len(items) on a page.
            "total": 1,
            "page": 1,
            "page_size": 25,
        },
        tables_read=("marketplace_submissions",),
    ),
    SuccessCase(
        endpoint="get_own_submission",
        label="rows",
        rows={"marketplace_submissions": [A_SUBMISSION_ROW]},
        status_code=200,
        body={"submission": A_SUBMISSION_ROW},
        tables_read=("marketplace_submissions",),
    ),
    SuccessCase(
        # Absent OR another owner's — Requirement 21.4's single 404 shape, from a read that
        # COMPLETED. Converting this into a 503 would make a cross-tenant probe distinguishable
        # from a probe for a Submission that does not exist, which is the whole point of the
        # requirement. It is pinned by code rather than by body because the envelope carries a
        # per-request identifier.
        endpoint="get_own_submission",
        label="read_completed_not_the_callers",
        rows={"marketplace_submissions": []},
        status_code=404,
        code=errors.MARKETPLACE_SUBMISSION_NOT_FOUND,
        tables_read=("marketplace_submissions",),
    ),
    SuccessCase(
        endpoint="admin_submission_detail",
        label="rows",
        rows={
            "marketplace_submissions": [A_SUBMISSION_ROW],
            "marketplace_backtest_evidence": [AN_EVIDENCE_ROW],
            "marketplace_submission_transitions": [A_TRANSITION_ROW],
        },
        status_code=200,
        body={
            "submission": A_SUBMISSION_ROW,
            "evidence": [AN_EVIDENCE_ROW],
            "transitions": [A_TRANSITION_ROW],
        },
        tables_read=(
            "marketplace_submissions",
            "marketplace_backtest_evidence",
            "marketplace_submission_transitions",
        ),
    ),
)


def _drive_success(case: SuccessCase) -> Answer:
    """Drive one endpoint against a client whose reads succeed. Not memoised — each case scripts
    its own rows, so there is nothing to share."""
    stub = _SucceedingClient(case.rows)
    library_router._CACHED_CATEGORIES = None

    app.dependency_overrides[get_current_user] = lambda: A_USER
    app.dependency_overrides[get_admin_user] = lambda: AN_ADMIN
    app.dependency_overrides[require_marketplace_access] = lambda: True
    try:
        with patch.object(
            library_router, "_build_service_client", return_value=stub
        ), patch.object(
            library_router, "_get_service_client", return_value=stub
        ), patch.object(
            library_router.redis_manager, "get", new=_cache_miss
        ), patch.object(
            library_router.redis_manager, "set", new=_cache_write
        ):
            endpoint = READ_ENDPOINTS[case.endpoint]
            response = client.request(endpoint.method, endpoint.path)
    finally:
        app.dependency_overrides.clear()
        library_router._CACHED_CATEGORIES = None

    try:
        body = response.json()
    except ValueError:  # pragma: no cover - a non-JSON body is itself a finding
        body = None
    return Answer(
        status_code=response.status_code,
        text=response.text,
        body=body,
        reads_attempted=tuple(stub.reads_attempted),
    )


def test_every_success_case_pins_an_answer():
    """A guard on the guard: a case that pinned neither a body nor a code would assert only the
    status, and a handler could change its whole response without failing anything."""
    for case in SUCCESS_CASES:
        assert case.endpoint in READ_ENDPOINTS, (
            f"SUCCESS_CASES names {case.endpoint!r}, which is not an enumerated read endpoint"
        )
        assert (case.body is None) != (case.code is None), (
            f"{case.endpoint}-{case.label} must pin exactly one of body/code; it pins "
            f"body={case.body!r} code={case.code!r}"
        )


def test_the_success_cases_cover_every_endpoint_task_21_3_changed():
    """The nine endpoints struck from :data:`PENDING` are the nine this section must pin.

    Written as a literal set rather than read back from the (now empty) registry: the registry no
    longer records which endpoints changed, and a list that could shrink silently would let a
    revert of one fix go unwatched.
    """
    changed_by_task_21_3 = {
        "get_strategy_reviews",
        "get_categories",
        "check_deployment_permission_endpoint",
        "my_library",
        "admin_pending_strategies",
        "get_subscription_status",
        "admin_submission_detail",
        "admin_list_submissions",
        "get_own_submission",
    }
    covered = {case.endpoint for case in SUCCESS_CASES}
    assert changed_by_task_21_3 <= covered, (
        f"task 21.3 changed {sorted(changed_by_task_21_3 - covered)} without pinning its success "
        f"path; a read-failure fix that moved the success path would pass unnoticed"
    )
    assert not PENDING, (
        f"PENDING is expected to be empty; it still holds {sorted(PENDING)}. Every entry is a "
        f"known defect — fix the endpoint, do not park it here"
    )


@pytest.mark.parametrize(
    "case", SUCCESS_CASES, ids=[f"{c.endpoint}-{c.label}" for c in SUCCESS_CASES]
)
def test_a_successful_read_still_answers_exactly_what_it_answered_before(case: SuccessCase):
    """Requirement 30.5: the nine fixes changed the failure path and nothing else.

    The whole ``(status, body)`` is compared, not a subset, so an ADDED key fails here too — a
    response that grows a field is a contract change whether or not any existing field moved.
    """
    answer = _drive_success(case)

    assert answer.status_code == case.status_code, (
        f"{case.endpoint} ({case.label}) answered {answer.status_code} for a read that "
        f"COMPLETED; it answered {case.status_code} before task 21.3. The read-failure fix moved "
        f"the success path. Body: {answer.text[:400]!r}"
    )

    assert answer.reads_attempted == case.tables_read, (
        f"{case.endpoint} ({case.label}) read {list(answer.reads_attempted)}; it read "
        f"{list(case.tables_read)} before. A changed round-trip set is a changed read, not a "
        f"changed error path (Requirements 27.1, 27.2)"
    )

    if case.body is not None:
        assert answer.body == case.body, (
            f"{case.endpoint} ({case.label}) answered a different body for a read that "
            f"COMPLETED.\n  expected: {case.body!r}\n  actual:   {answer.body!r}"
        )
    else:
        assert _code_of(answer.body) == case.code, (
            f"{case.endpoint} ({case.label}) answered code {_code_of(answer.body)!r}, not the "
            f"{case.code!r} it answered before. Body: {answer.text[:400]!r}"
        )
        assert set(answer.body) == set(errors.ERROR_ENVELOPE_KEYS), (
            f"{case.endpoint} ({case.label}) answered keys {sorted(answer.body)}; the declared "
            f"envelope is {sorted(errors.ERROR_ENVELOPE_KEYS)}"
        )


def test_a_completed_read_that_finds_nothing_is_never_answered_as_a_read_failure():
    """The one-line statement of what this section is for, over the three empty-result cases.

    A fix for "the read did not complete" that also caught "the read completed and matched
    nothing" would answer 503 ``MARKETPLACE_READ_FAILED`` here. It is asserted separately from the
    table above because it is the failure mode of the *fix*, and it should be legible as such
    rather than as one row of a parametrised comparison.
    """
    empty_result_cases = [
        case for case in SUCCESS_CASES if case.label.startswith("read_completed")
    ]
    assert len(empty_result_cases) == 3, (
        f"expected the three empty-result cases; found {[c.label for c in empty_result_cases]}"
    )
    for case in empty_result_cases:
        answer = _drive_success(case)
        assert _code_of(answer.body) != errors.MARKETPLACE_READ_FAILED, (
            f"{case.endpoint} ({case.label}) answered MARKETPLACE_READ_FAILED for a read that "
            f"COMPLETED and matched no row. That zero was measured: reporting it as a read "
            f"failure is the mirror image of the defect task 21.3 fixed"
        )
        assert answer.status_code not in (500, 503), (
            f"{case.endpoint} ({case.label}) answered {answer.status_code} for a read that "
            f"COMPLETED and matched no row"
        )


def test_a_read_failure_never_becomes_an_admission():
    """The constraint that outranks every other assertion in this file.

    ``check_deployment_permission`` now REFUSES a read it cannot complete. The one way that fix
    could be worse than the defect is if refusing had been implemented as granting, so the four
    failure modes are checked for ``has_permission: true`` explicitly rather than inferred from
    the 503. Nothing about a deployment gate is left to inference (Requirements 7.5, 28.3).
    """
    for mode in sorted(FAILURE_MODES):
        answer = _answer("check_deployment_permission_endpoint", mode)
        assert answer.status_code in (500, 503), (
            f"the deploy check answered {answer.status_code} on a {mode}"
        )
        assert "has_permission" not in answer.text, (
            f"the deploy check's {mode} body carries has_permission: a read that did not "
            f"complete decided a deployment gate. Body: {answer.text[:400]!r}"
        )
        assert "true" not in answer.text.lower(), (
            f"the deploy check's {mode} body carries a truthy flag; a read failure must never "
            f"become an admission. Body: {answer.text[:400]!r}"
        )
