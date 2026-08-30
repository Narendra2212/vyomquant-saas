"""
tests/test_library_route_resolution.py — Strategy Library route resolution guard

This file is the **revert-detector for Task 13.1** of the
`marketplace-subscriptions-paper-trading` spec.

Task 13.1 moved every path-parameter-free route in `backend_app/routers/library.py`
onto a dedicated `literal_router = APIRouter()` and, at the bottom of that module,
spliced those routes in front of the parameterised ones:

    router.routes[:0] = literal_router.routes

FastAPI/Starlette resolve a request against `app.router.routes` in registration
order, so before 13.1 the parameterised `GET /creator/{creator_id}` and
`GET /{library_id}` were declared first and permanently swallowed
`GET /creator/analytics`, `GET /recommendations` and `GET /favorites`
(the first answered 422 from `_safe_uuid("analytics", "creator_id")`, the other
two matched `{library_id}` and answered 422 as well).

Nothing in the module structure forces that ordering to survive: a future edit
that moves a decorator back onto `router`, deletes the splice line, or reorders
the splice will silently re-shadow those endpoints and every handler will still
import cleanly. The assertions below resolve each path exactly the way Starlette
does — walking `app.router.routes` in order and taking the first full match — and
name the endpoint function that must win. A reordering therefore fails here
instead of shipping unreachable endpoints.

Requirements: 1.3 (a Marketplace handler must actually be the one that runs a
query), 22.1 (every non-catalogue endpoint applies its authorisation dependency,
which it cannot do while shadowed by a different handler).

Verification: pytest tests/test_library_route_resolution.py
              tests/test_router_registration_completeness.py
"""

import os
import sys
import uuid

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Match

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

LIBRARY_PREFIX = "/api/library"

# Two arbitrary but valid UUIDs — the parameterised routes run `_safe_uuid`, so
# resolution assertions for them must use a value a real client could send.
SAMPLE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "library-strategy"))
SAMPLE_CREATOR_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "library-creator"))


@pytest.fixture(scope="module")
def app():
    """The real FastAPI app, mounted exactly as production mounts it."""
    from backend_app.main import app as fastapi_app

    return fastapi_app


def resolve(app, method: str, path: str):
    """
    Return the route Starlette would dispatch (method, path) to.

    Mirrors `starlette.routing.Router.app`: walk the route table in order and
    take the first FULL match. Returns None when nothing fully matches.
    """
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in app.router.routes:
        match, _child_scope = route.matches(scope)
        if match == Match.FULL:
            return route
    return None


def resolved_endpoint_name(app, method: str, path: str) -> str:
    route = resolve(app, method, path)
    assert route is not None, f"{method} {path} resolves to no route at all"
    # `@limiter.limit` wraps some handlers, and functools.wraps preserves
    # __name__, so the name is the stable identity to compare on.
    return getattr(route, "name", None) or route.endpoint.__name__


def library_routes(app):
    return [
        route
        for route in app.router.routes
        if isinstance(route, APIRoute) and route.path.startswith(LIBRARY_PREFIX)
    ]


def is_literal(path: str) -> bool:
    return "{" not in path


# ─────────────────────────────────────────────────────────────────────────────
# The three endpoints Task 13.1 unshadowed
# ─────────────────────────────────────────────────────────────────────────────


class TestUnshadowedEndpoints:
    """These three assertions fail on the pre-13.1 registration order."""

    def test_creator_analytics_is_not_shadowed_by_creator_profile(self, app):
        assert (
            resolved_endpoint_name(app, "GET", f"{LIBRARY_PREFIX}/creator/analytics")
            == "creator_analytics"
        ), (
            "GET /api/library/creator/analytics resolved to a different handler — "
            "GET /creator/{creator_id} is being matched first again, so the endpoint "
            "answers 422 from _safe_uuid('analytics', 'creator_id')."
        )

    def test_recommendations_is_not_shadowed_by_library_detail(self, app):
        assert (
            resolved_endpoint_name(app, "GET", f"{LIBRARY_PREFIX}/recommendations")
            == "get_recommendations"
        ), (
            "GET /api/library/recommendations resolved to a different handler — "
            "GET /{library_id} is being matched first again."
        )

    def test_favorites_is_not_shadowed_by_library_detail(self, app):
        assert (
            resolved_endpoint_name(app, "GET", f"{LIBRARY_PREFIX}/favorites")
            == "get_user_favorites"
        ), (
            "GET /api/library/favorites resolved to a different handler — "
            "GET /{library_id} is being matched first again."
        )

    def test_none_of_the_three_resolve_to_the_parameterised_handlers(self, app):
        shadowers = {"get_creator_profile", "get_library_detail"}
        for path in (
            f"{LIBRARY_PREFIX}/creator/analytics",
            f"{LIBRARY_PREFIX}/recommendations",
            f"{LIBRARY_PREFIX}/favorites",
        ):
            assert resolved_endpoint_name(app, "GET", path) not in shadowers, (
                f"GET {path} is swallowed by a parameterised route"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Every literal path the router exposes
# ─────────────────────────────────────────────────────────────────────────────

# Explicit table of the literal surface as it stands. Kept alongside the
# dynamic sweep below so that a *renamed* handler is caught too, not just a
# reordered one.
LITERAL_ROUTE_EXPECTATIONS = [
    ("GET", "", "browse_library"),
    ("POST", "", "publish_strategy"),
    ("GET", "/featured", "get_featured_strategies"),
    ("GET", "/trending", "get_trending_strategies"),
    ("GET", "/categories", "get_categories"),
    ("GET", "/me", "my_library"),
    ("GET", "/admin/pending", "admin_pending_strategies"),
    ("GET", "/creator/analytics", "creator_analytics"),
    ("GET", "/subscriber/analytics", "subscriber_analytics"),
    ("GET", "/recommendations", "get_recommendations"),
    ("GET", "/favorites", "get_user_favorites"),
    ("POST", "/compare", "compare_strategies"),
]


class TestLiteralRouteResolution:
    @pytest.mark.parametrize(
        "method,suffix,expected", LITERAL_ROUTE_EXPECTATIONS,
        ids=[f"{m} {s or '/'}" for m, s, _ in LITERAL_ROUTE_EXPECTATIONS],
    )
    def test_literal_path_resolves_to_its_own_handler(self, app, method, suffix, expected):
        assert (
            resolved_endpoint_name(app, method, f"{LIBRARY_PREFIX}{suffix}") == expected
        ), (
            f"{method} {LIBRARY_PREFIX}{suffix} no longer resolves to {expected}; "
            "a parameterised route is shadowing it."
        )

    def test_every_literal_route_in_the_table_resolves_to_itself(self, app):
        """
        Dynamic sweep: whatever literal routes `library.py` declares — including
        ones added after this test was written, such as the Task 14.4 submission
        routes — each must be the first full match for its own path.
        """
        mismatches = []
        for route in library_routes(app):
            if not is_literal(route.path):
                continue
            for method in sorted(route.methods or set()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                winner = resolve(app, method, route.path)
                winner_name = (
                    getattr(winner, "name", None) or winner.endpoint.__name__
                ) if winner is not None else None
                if winner_name != route.name:
                    mismatches.append(
                        f"{method} {route.path}: declared handler '{route.name}' "
                        f"but request resolves to '{winner_name}'"
                    )
        assert not mismatches, (
            "Literal Strategy Library routes are shadowed by parameterised routes:\n  "
            + "\n  ".join(mismatches)
            + "\n\nTask 13.1 registers every literal route on `literal_router` and splices "
            "it in front via `router.routes[:0] = literal_router.routes` at the bottom of "
            "backend_app/routers/library.py. Restore that ordering."
        )

    def test_literal_routes_precede_parameterised_routes_in_the_table(self, app):
        """Structural form of the same guarantee, independent of path matching."""
        routes = library_routes(app)
        literal_positions = [i for i, r in enumerate(routes) if is_literal(r.path)]
        param_positions = [i for i, r in enumerate(routes) if not is_literal(r.path)]
        assert literal_positions and param_positions, (
            "Expected the library router to expose both literal and parameterised routes"
        )
        assert max(literal_positions) < min(param_positions), (
            "A literal Strategy Library route is registered after a parameterised one: "
            f"last literal at index {max(literal_positions)} "
            f"({routes[max(literal_positions)].path}), first parameterised at index "
            f"{min(param_positions)} ({routes[min(param_positions)].path}). "
            "Literal routes must be spliced in front (Task 13.1)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# The parameterised routes must keep resolving exactly as they do today
# ─────────────────────────────────────────────────────────────────────────────


class TestParameterisedRoutesUnchanged:
    def test_library_detail_still_resolves(self, app):
        assert (
            resolved_endpoint_name(app, "GET", f"{LIBRARY_PREFIX}/{SAMPLE_ID}")
            == "get_library_detail"
        )

    def test_creator_profile_still_resolves(self, app):
        assert (
            resolved_endpoint_name(
                app, "GET", f"{LIBRARY_PREFIX}/creator/{SAMPLE_CREATOR_ID}"
            )
            == "get_creator_profile"
        )

    @pytest.mark.parametrize(
        "method,template,expected",
        [
            ("DELETE", "/{id}", "unpublish_strategy"),
            ("POST", "/{id}/clone", "clone_strategy"),
            ("POST", "/{id}/rate", "rate_strategy"),
            ("PATCH", "/admin/{id}", "admin_moderate_strategy"),
            ("POST", "/{id}/checkout", "create_marketplace_checkout"),
            ("GET", "/{id}/subscribe", "get_subscription_status"),
            ("GET", "/{id}/deploy/check", "check_deployment_permission_endpoint"),
            ("POST", "/{id}/deploy", "deploy_marketplace_strategy"),
            ("GET", "/{id}/reviews", "get_strategy_reviews"),
            ("POST", "/{id}/favorite", "favorite_strategy"),
            ("DELETE", "/{id}/favorite", "unfavorite_strategy"),
            ("POST", "/subscriptions/{id}/cancel", "cancel_subscription"),
            ("POST", "/subscriptions/{id}/renew", "renew_subscription"),
        ],
    )
    def test_parameterised_routes_resolve_to_their_own_handlers(
        self, app, method, template, expected
    ):
        path = LIBRARY_PREFIX + template.replace("{id}", SAMPLE_ID)
        assert resolved_endpoint_name(app, method, path) == expected, (
            f"{method} {path} no longer resolves to {expected}; putting the literal "
            "routes in front must not change parameterised resolution."
        )


# ─────────────────────────────────────────────────────────────────────────────
# No duplicates, nothing lost in the splice
# ─────────────────────────────────────────────────────────────────────────────


class TestSpliceIntegrity:
    def test_no_method_path_pair_is_registered_twice(self, app):
        seen = {}
        duplicates = []
        for route in library_routes(app):
            for method in sorted(route.methods or set()):
                key = (method, route.path)
                if key in seen:
                    duplicates.append(f"{method} {route.path}: {seen[key]} and {route.name}")
                else:
                    seen[key] = route.name
        assert not duplicates, (
            "Duplicate Strategy Library routes — `literal_router.routes` was appended "
            "instead of spliced, or included twice:\n  " + "\n  ".join(duplicates)
        )

    def test_every_expected_literal_route_is_registered(self, app):
        registered = {
            (method, route.path)
            for route in library_routes(app)
            for method in (route.methods or set())
        }
        missing = [
            f"{method} {LIBRARY_PREFIX}{suffix}"
            for method, suffix, _ in LITERAL_ROUTE_EXPECTATIONS
            if (method, f"{LIBRARY_PREFIX}{suffix}") not in registered
        ]
        assert not missing, (
            "Literal Strategy Library routes are missing from the app entirely — "
            "`literal_router` was declared but never spliced into `router`: "
            f"{missing}"
        )
