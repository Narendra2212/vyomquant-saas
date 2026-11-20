"""LIB-04 — a client that raises anything must answer 503, never escape to a 500.

WHAT WAS WRONG, AND IT WAS INTRODUCED BY THE LIB-01 REPAIR
-----------------------------------------------------------
LIB-01 narrowed this module's broad ``except Exception`` handlers to
``except _PostgrestAPIError``. That was the right direction — a broad handler had been
relabelling an ``AttributeError`` from a ``None`` client as *"Failed to look up library
entry."*, a statement about a row that was not the problem — and it introduced a different
defect: **any failure that is not a PostgREST ``APIError`` escaped the handler entirely**
and reached the application's generic 500.

That is not an edge case. ``supabase-py`` performs the request with ``httpx``, and the
commonest real read failure — DNS, TLS, connection reset, read timeout — arrives as
``httpx.ConnectError`` / ``httpx.ReadTimeout`` / ``httpx.RemoteProtocolError``, none of
which is an ``APIError``. So the narrowing turned *"the database was unreachable"* from a
retryable 503 into a 500 that blames nothing in particular.

HOW IT WAS FOUND, WHICH IS THE POINT
------------------------------------
Not by reading the code. By the **PostgreSQL lane** of the Phase 7 full regression, which
answered ``500 INTERNAL_SERVER_ERROR`` on ``POST /api/library/{id}/clone`` and
``DELETE /api/library/{id}`` across eleven tests in four files — including the two
``test_validation_sweep`` cells the LIB-01 repair had just turned green. The escaping
exception was ``psycopg2.InterfaceError: connection already closed``.

Every one of those files passed in isolation and in every targeted grouping, because the
trigger was a dead client left behind by an *earlier* test in the same process (a
save/restore race on a module global across two threads, fixed in
``tests/test_lib01_clone_strategy_postgres.py``). Different transport, same shape as the
production case: the client raised something that is not an ``APIError``.

WHAT THIS FILE ASSERTS
----------------------
That the route's answer does not depend on WHICH exception the client raised. From outside,
a read that did not complete is a 503 — ``MARKETPLACE_READ_FAILED``'s public sentence
already says exactly that — and the exception type belongs in the log, with a traceback,
not in the status code.

No database, no network, no credential: the failure is injected, because the property is
about the handler's response to a failing client and not about any particular client.
"""

import asyncio
import uuid

import pytest

import backend_app.routers.library as lib
from backend_app.backend.marketplace.errors import (MARKETPLACE_READ_FAILED,
                                                    MarketplaceError)


class _ExplodingQuery:
    """Accepts the whole PostgREST builder surface and raises on ``execute()``."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def select(self, *_a, **_k):
        return self

    def insert(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return self

    def upsert(self, *_a, **_k):
        return self

    def delete(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def single(self, *_a, **_k):
        return self

    def execute(self):
        raise self._error


class _ExplodingClient:
    """A service-role client that fails the way a real one fails at the transport layer."""

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.calls = 0

    def table(self, _name: str):
        self.calls += 1
        return _ExplodingQuery(self._error)


def _transport_failures():
    """The exception types a real client actually raises, plus two controls.

    ``httpx`` is what ``supabase-py`` uses, so its errors are the realistic cases.
    ``psycopg2.InterfaceError`` is the one the full regression actually observed.
    ``RuntimeError`` and ``AttributeError`` are controls: the property is that the answer
    does NOT depend on the type, so an unforeseen type has to be covered too — that is the
    whole lesson of this finding.
    """
    cases = []

    try:
        import httpx

        cases += [
            pytest.param(httpx.ConnectError("name resolution failed"), id="httpx.ConnectError"),
            pytest.param(httpx.ReadTimeout("read timed out"), id="httpx.ReadTimeout"),
            pytest.param(
                httpx.RemoteProtocolError("server disconnected"),
                id="httpx.RemoteProtocolError",
            ),
        ]
    except Exception:  # pragma: no cover - httpx is a supabase dependency, so present
        pass

    try:
        import psycopg2

        cases.append(
            pytest.param(
                psycopg2.InterfaceError("connection already closed"),
                id="psycopg2.InterfaceError",
            )
        )
    except Exception:  # pragma: no cover
        pass

    cases += [
        pytest.param(RuntimeError("something nobody foresaw"), id="RuntimeError"),
        pytest.param(
            AttributeError("'NoneType' object has no attribute 'table'"),
            id="AttributeError",
        ),
        pytest.param(ValueError("a parsing fault inside the client"), id="ValueError"),
        pytest.param(KeyError("a column name the client did not expect"), id="KeyError"),
    ]
    return cases


@pytest.fixture(autouse=True)
def _rate_limiter_off(monkeypatch):
    """Stand ``slowapi``'s decorator down for a direct handler call.

    The same seam ``tests/test_entitlement_matrix.py`` uses, and for the same reason: the
    wrapper demands a genuine ``starlette.requests.Request``. Coverage of the decorator's
    presence stays in ``tests/test_task_33_2_rate_limit_keys.py``.
    """
    from backend_app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False, raising=False)


@pytest.fixture(autouse=True)
def _audit_logger_stubbed(monkeypatch):
    class _Recorder:
        async def log(self, _action, **_kwargs):
            return None

    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: _Recorder())


def _undecorated(handler):
    """The handler itself, with every ``functools.wraps`` layer removed.

    The two routes do NOT carry the same number of decorators - ``clone_strategy`` has two
    and ``unpublish_strategy`` has one - so a hard-coded ``__wrapped__.__wrapped__`` works
    for one and raises ``AttributeError: 'function' object has no attribute '__wrapped__'``
    for the other. Walking until there is nothing left to unwrap is correct for both and
    stays correct when a decorator is added or removed.
    """
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


def _clone(client, monkeypatch):
    monkeypatch.setattr(lib, "_require_service_client", lambda: client)
    return asyncio.run(
        _undecorated(lib.clone_strategy)(
            request=None,
            library_id=str(uuid.uuid4()),
            user={"id": str(uuid.uuid4())},
            _strategy_quota=None,
        )
    )


def _unpublish(client, monkeypatch):
    """``unpublish_strategy(library_id, user)`` - no ``request`` parameter.

    ``clone_strategy`` takes one (it is rate-limited, and ``slowapi`` requires a genuine
    ``Request``); ``unpublish_strategy`` does not. The two handlers' signatures are read
    from the source rather than assumed to match.
    """
    monkeypatch.setattr(lib, "_require_service_client", lambda: client)
    return asyncio.run(
        _undecorated(lib.unpublish_strategy)(
            library_id=str(uuid.uuid4()),
            user={"id": str(uuid.uuid4())},
        )
    )


# ══════════════════════════════════════════════════════════════════════════════════════════
#  THE PROPERTY: THE ANSWER DOES NOT DEPEND ON THE EXCEPTION TYPE
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("error", _transport_failures())
def test_clone_answers_503_whatever_the_client_raised(error, monkeypatch):
    client = _ExplodingClient(error)

    with pytest.raises(MarketplaceError) as caught:
        _clone(client, monkeypatch)

    assert client.calls >= 1, "the handler never reached the client, so nothing was tested"
    assert caught.value.code == MARKETPLACE_READ_FAILED
    assert caught.value.http_status == 503, (
        f"a {type(error).__name__} from the service client escaped as something other than "
        f"a retryable 503"
    )


@pytest.mark.parametrize("error", _transport_failures())
def test_unpublish_answers_503_whatever_the_client_raised(error, monkeypatch):
    client = _ExplodingClient(error)

    with pytest.raises(MarketplaceError) as caught:
        _unpublish(client, monkeypatch)

    assert client.calls >= 1
    assert caught.value.code == MARKETPLACE_READ_FAILED
    assert caught.value.http_status == 503


def test_the_public_sentence_names_no_internals(monkeypatch):
    """The 503 reaches a customer, so it must not carry the exception text.

    The injected message is deliberately the sort of thing that must not be echoed: a
    hostname and a driver type.
    """
    error = RuntimeError(
        "could not connect to db.kqmbvtzrldnjfwhsepxg.supabase.co:6543 via psycopg2"
    )
    client = _ExplodingClient(error)

    with pytest.raises(MarketplaceError) as caught:
        _clone(client, monkeypatch)

    message = caught.value.message
    assert "supabase.co" not in message
    assert "psycopg2" not in message
    assert "6543" not in message
    assert "RuntimeError" not in message
    # It still says something actionable.
    assert "try again" in message.lower()


def test_a_postgrest_api_error_still_takes_its_own_handler(monkeypatch):
    """The narrow handler is not replaced by the broad one, and the order matters.

    ``_PostgrestAPIError`` is listed FIRST at every call site so the log records the
    database's own answer, which is the diagnosis when the database answered. The broad
    handler exists for the case where nothing answered at all. Both produce the same 503 —
    that is the point — so this asserts the status AND that the narrow type is still
    reachable, which it would not be if somebody collapsed the two handlers into one.
    """
    import ast
    import inspect
    import textwrap

    # PER TRY, not by string offset over the whole function. A global
    # `source.index("except Exception")` finds step 3's audit-logging handler - which is
    # unrelated, deliberate, and sits BEFORE the first client call - and reports the order
    # as inverted when it is not. Measured: 3586 < 5240, a false failure.
    tree = ast.parse(textwrap.dedent(inspect.getsource(_undecorated(lib.clone_strategy))))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        reaches_client = any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "execute"
            for child in node.body
            for sub in ast.walk(child)
        )
        if not reaches_client:
            continue
        positions = {}
        for index, handler in enumerate(node.handlers):
            text = ast.unparse(handler.type) if handler.type is not None else "bare"
            if "_PostgrestAPIError" in text:
                positions.setdefault("narrow", index)
            elif text in ("Exception", "bare"):
                positions.setdefault("broad", index)
        if "narrow" in positions and "broad" in positions:
            checked += 1
            assert positions["narrow"] < positions["broad"], (
                f"the broad handler precedes the narrow one at line {node.lineno}, so a "
                f"PostgREST APIError would be logged without the database's own answer"
            )
    assert checked >= 5, f"only {checked} guarded client calls were inspected"

    client = _ExplodingClient(lib._PostgrestAPIError({"message": "schema cache miss"}))
    with pytest.raises(MarketplaceError) as caught:
        _clone(client, monkeypatch)
    assert caught.value.http_status == 503


def test_every_client_call_on_the_clone_path_is_guarded():
    """Structural, because a new unguarded ``.execute()`` is how this comes back.

    Counts ``execute()`` calls inside ``clone_strategy`` and requires each to be inside a
    ``try`` that has BOTH handlers. Asserted on the source rather than by exercising every
    step, because reaching step 9 requires an entitling subscription and a provisioned
    database, and a structural check is the one that stays true when those are absent.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(_undecorated(lib.clone_strategy))))

    guarded, unguarded = 0, []

    class _Visitor(ast.NodeVisitor):
        def visit_Try(self, node: ast.Try) -> None:
            handlers = {
                ast.unparse(h.type) if h.type is not None else "bare"
                for h in node.handlers
            }
            has_narrow = any("_PostgrestAPIError" in h for h in handlers)
            has_broad = any(h == "Exception" or h == "bare" for h in handlers)
            for child in node.body:
                for sub in ast.walk(child):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "execute"
                    ):
                        nonlocal guarded
                        if has_narrow and has_broad:
                            guarded += 1
                        else:
                            unguarded.append((sub.lineno, sorted(handlers)))
            # Visit the handlers and else/finally too, but not the body again.
            for handler in node.handlers:
                self.generic_visit(handler)

        def visit_Call(self, node: ast.Call) -> None:
            self.generic_visit(node)

    visitor = _Visitor()
    visitor.visit(tree)

    # Any `.execute()` NOT inside a try at all.
    in_try = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in node.body:
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        in_try.add(id(sub))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and id(node) not in in_try
        ):
            unguarded.append((node.lineno, ["NOT IN ANY TRY"]))

    assert guarded >= 5, (
        f"only {guarded} guarded client calls found on the clone path; the structure this "
        f"test reads has changed"
    )
    assert unguarded == [], (
        f"client calls on the clone path are not guarded by both handlers: {unguarded}"
    )
