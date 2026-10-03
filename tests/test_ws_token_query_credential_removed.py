"""
tests/test_ws_token_query_credential_removed.py
production-launch-hardening task 13.25. Requirements 1.21, 2.21, 3.9.

WHAT THIS GUARDS, AND WHY IT IS STRUCTURAL RATHER THAN TEXTUAL
--------------------------------------------------------------
Uvicorn's access log writes the full WebSocket request line **including the query
string**, so for as long as any route accepted ``?token=`` a session JWT was written to
CloudWatch beside the user id and the client IP:

    [2026-09-28 02:04:11 +0000] [41] [INFO] ('10.0.0.101', 29036) -
      "WebSocket /ws/user/52384fe1-…-5c36dd8a2bf8?token=<JWT>" 403

Task 13.25 removed that arm. This file asserts it stays removed.

A GREP FOR "token" CANNOT DO THIS JOB, which is the whole reason for the AST. The word
appears in ``ws_routes.py`` in prose (the header block quotes the very log line above),
in unrelated identifiers (``_decode_hs256_token``, ``_validate_ws_token``,
``access_token``), and in log messages ("Token validation failed"). A text scan either
false-positives on all of that or gets tuned until it is vacuous. So the questions are
asked of the parse tree instead:

  1. No function decorated ``@ws_router.websocket(...)`` declares a parameter named
     exactly ``token`` — that parameter *is* the credential surface, because FastAPI
     binds it from the query string.
  2. No such function mentions the bare name ``token`` in its body at all, which also
     catches a route that stopped declaring the parameter but reads
     ``websocket.query_params["token"]`` instead.
  3. Neither ``_resolve_ws_credential`` nor ``_resolve_ws_subject`` declares or
     references ``token``. These are the two helpers every route resolves through, so a
     branch here would re-admit the credential on all nine at once.

WHAT IS DELIBERATELY STILL ALLOWED. ``_validate_ws_token(token, claimed_user_id)``
keeps its ``token`` parameter and is explicitly exempted below — by name, so the
exemption is a decision in the diff rather than a hole in the pattern. It is not a route
and not a resolver: nothing reachable from a handshake calls it, and what it validates is
a JWT handed over in a message *body*, which is not access-logged, not in browser history
and not in a ``Referer``. Assertion 4 pins that exemption to exactly one function, so a
second one cannot be added quietly.

OBSERVED FAILING BEFORE THE CHANGE. Against ``fe1dc6a3``'s ``ws_routes.py``: assertion 1
reported all nine routes carrying ``token``, assertion 2 reported nine bodies referencing
it, and assertion 3 reported both helpers declaring and referencing it.
"""

import ast
from pathlib import Path

import pytest

#: The module under guard. Resolved from this file so the test does not depend on cwd.
WS_ROUTES_PATH = (
    Path(__file__).resolve().parent.parent / "backend_app" / "api_ws" / "ws_routes.py"
)

#: The credential query parameter that must not come back.
FORBIDDEN_QUERY_CREDENTIAL = "token"

#: The two helpers every route resolves its credential through.
CREDENTIAL_RESOLVERS = ("_resolve_ws_credential", "_resolve_ws_subject")

#: The ONE function allowed to take a ``token`` argument, and why: see the module
#: docstring. Not a route, not a resolver, and not fed from a URL.
TOKEN_ARGUMENT_EXEMPTIONS = frozenset({"_validate_ws_token"})

#: Pinned so that *removing* a route shows up here as well. Nine is what task 8.2 built
#: and what task 13.25 left standing — 13.25 removed a parameter, not a route.
EXPECTED_WEBSOCKET_ROUTE_COUNT = 9


def _module_tree() -> ast.Module:
    return ast.parse(WS_ROUTES_PATH.read_text(encoding="utf-8"), str(WS_ROUTES_PATH))


def _is_ws_route_decorator(node: ast.expr) -> bool:
    """True for ``@ws_router.websocket(...)``.

    Only the called form counts: ``ws_router.websocket`` without a call is not a route
    registration, and matching it would be matching a reference rather than a mount.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "websocket"
        and isinstance(func.value, ast.Name)
        and func.value.id == "ws_router"
    )


def _websocket_routes(tree: ast.Module) -> list:
    """Every function mounted as a WebSocket route, sync or async."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_ws_route_decorator(d) for d in node.decorator_list)
    ]


def _parameter_names(func) -> list:
    """Every declared parameter name, positional, keyword-only and starred alike."""
    args = func.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def _referenced_names(func) -> set:
    """Bare names and string subscripts used anywhere inside ``func``.

    The string literals are collected too, so ``query_params["token"]`` is caught: a
    route that stops *declaring* the parameter but reads it out of the query mapping by
    hand has not removed the credential, it has only hidden it from the signature.
    """
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _named_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return _module_tree()


@pytest.fixture(scope="module")
def routes(tree) -> list:
    return _websocket_routes(tree)


class TestNoWebSocketRouteAcceptsATokenQueryParameter:
    """Assertion 1 and 2: the nine route signatures and the nine route bodies."""

    def test_the_route_roster_is_the_size_it_was(self, routes):
        # Guards the guard: if the routes stopped being discoverable this way, every
        # assertion below would pass vacuously over an empty list.
        assert len(routes) == EXPECTED_WEBSOCKET_ROUTE_COUNT, (
            f"expected {EXPECTED_WEBSOCKET_ROUTE_COUNT} @ws_router.websocket routes in "
            f"{WS_ROUTES_PATH.name}, found {len(routes)}: "
            f"{sorted(r.name for r in routes)}. Task 13.25 removed a query parameter, "
            f"not a route — a change in this number is a different change."
        )

    def test_no_route_declares_a_token_parameter(self, routes):
        offenders = {
            route.name: _parameter_names(route)
            for route in routes
            if FORBIDDEN_QUERY_CREDENTIAL in _parameter_names(route)
        }
        assert not offenders, (
            "these WebSocket routes declare a `token` query parameter, which FastAPI "
            "binds from the query string and uvicorn's access log then writes to "
            f"CloudWatch verbatim: {offenders}. Task 13.25 removed this arm; the "
            "credential is `?ticket=`."
        )

    def test_no_route_reads_token_out_of_the_query_mapping(self, routes):
        offenders = sorted(
            route.name
            for route in routes
            if FORBIDDEN_QUERY_CREDENTIAL in _referenced_names(route)
        )
        assert not offenders, (
            "these WebSocket routes reference the bare name or string `token` in their "
            f"body: {offenders}. A route that reads `websocket.query_params['token']` "
            "has moved the credential out of the signature, not out of the URL."
        )

    def test_every_route_still_takes_a_ticket(self, routes):
        # The other half of the assertion. Removing `token` and leaving a route with no
        # credential at all would pass every test above and admit nothing — or worse,
        # admit everything. Each route must still have exactly one way in.
        missing = sorted(
            route.name for route in routes if "ticket" not in _parameter_names(route)
        )
        assert not missing, (
            f"these WebSocket routes accept no `ticket` parameter: {missing}. The "
            "ticket is the only socket credential since task 13.25."
        )


class TestNeitherCredentialResolverHasATokenBranch:
    """Assertion 3: the two helpers all nine routes resolve through."""

    @pytest.mark.parametrize("resolver", CREDENTIAL_RESOLVERS)
    def test_the_resolver_exists(self, tree, resolver):
        assert _named_function(tree, resolver) is not None, (
            f"`{resolver}` was not found in {WS_ROUTES_PATH.name}. It is what every "
            "route resolves its credential through; if it was renamed, this guard has "
            "to be pointed at the new name rather than left passing vacuously."
        )

    @pytest.mark.parametrize("resolver", CREDENTIAL_RESOLVERS)
    def test_the_resolver_declares_no_token_parameter(self, tree, resolver):
        func = _named_function(tree, resolver)
        assert FORBIDDEN_QUERY_CREDENTIAL not in _parameter_names(func), (
            f"`{resolver}` declares a `token` parameter. A token arm here re-admits the "
            "JWT on all nine routes at once, whatever their own signatures say."
        )

    @pytest.mark.parametrize("resolver", CREDENTIAL_RESOLVERS)
    def test_the_resolver_has_no_token_branch(self, tree, resolver):
        func = _named_function(tree, resolver)
        assert FORBIDDEN_QUERY_CREDENTIAL not in _referenced_names(func), (
            f"`{resolver}` still references `token` in its body — the branch task 13.25 "
            "deleted. Decoding a JWT here is decoding a credential that arrived in a URL."
        )


class TestTheOneExemptionStaysOne:
    """Assertion 4: `_validate_ws_token` is the only `token`-taking function left."""

    def test_the_exemption_is_still_exactly_the_documented_set(self, tree):
        taking_token = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and FORBIDDEN_QUERY_CREDENTIAL in _parameter_names(node)
        }
        assert taking_token == set(TOKEN_ARGUMENT_EXEMPTIONS), (
            "the set of functions in ws_routes.py taking a `token` argument changed. "
            f"Expected exactly {sorted(TOKEN_ARGUMENT_EXEMPTIONS)} (not a route, not a "
            "resolver, fed from a message body rather than a URL), found "
            f"{sorted(taking_token)}. A new entry needs the same argument made for it "
            "in writing, in this file."
        )

    def test_the_exemption_is_not_itself_a_route(self, tree, routes):
        route_names = {route.name for route in routes}
        overlap = route_names & set(TOKEN_ARGUMENT_EXEMPTIONS)
        assert not overlap, (
            f"{sorted(overlap)} is both exempted and mounted as a WebSocket route, "
            "which would make the exemption a hole in assertion 1."
        )


class TestTheHeaderRecordsTheClosure:
    """The prose is part of the control: the next reader must not re-add the arm.

    Pinned loosely — phrases, not paragraphs — because the point is that the block no
    longer reads as a pending decision, not that it is never edited again.
    """

    def test_the_grace_period_is_recorded_as_ended(self):
        text = WS_ROUTES_PATH.read_text(encoding="utf-8")
        assert "THE `token` ARM IS GONE" in text, (
            "the task 8.2 header must record that the arm was removed, not that its "
            "removal is scheduled"
        )
        assert "SCHEDULED FOR REMOVAL" not in text, (
            "the header still reads as a pending decision"
        )
        # The evidence, so the removal is auditable without going back to CloudWatch.
        #
        # Pinned on DATED facts and on the scoping that makes them trustworthy, rather
        # than on one count. This assertion used to require the string "136-to-2", and
        # that figure turned out to be an artefact of an unscoped CloudWatch term over a
        # multi-stream log group — so the test was pinning a false number into the build.
        # Task 13.27 records the correction. A date and a stated method are what survive
        # a re-measurement; a bare count is exactly what did not.
        assert "2026-10-02" in text, (
            "the header must name when the grace period ended"
        )
        assert "2026-10-01 20:45:06" in text, (
            "the header must carry the timestamp of the NEWEST JWT-bearing handshake in "
            "the log group. That date is the evidence the removal rests on - the "
            "exposure is dated, and it stopped before the arm was deleted - and it is "
            "corroborated by two independent probes (`token=` and `eyJ`)"
        )
        assert "log-stream-name-prefix" in text, (
            "the header must record that the handshake count was SCOPED to the API's own "
            "log streams. `/ecs/vyomquant-api` is a multi-stream group and an unscoped "
            "term counts QuestDB `[token=<view>]` lines as handshakes; without the "
            "scoping stated, the per-day table above is not auditable (task 13.27)"
        )
        assert "THERE WAS NO RESIDUAL" in text, (
            "the header must record that the legacy arm carried NO traffic when it was "
            "deleted. It used to claim a residual ~2/day of sessions each paying one "
            "4001 - an overstated, user-visible cost that never occurred - and that "
            "claim reverting is the regression worth catching (task 13.27)"
        )
        # NOT asserted: that the string "136-to-2" is absent. The header quotes the
        # withdrawn figure deliberately, to mark it withdrawn, so a negative text scan
        # fires on the correction itself. The three positive assertions above already
        # fail if the block reverts - none of their strings exists in the old text.
