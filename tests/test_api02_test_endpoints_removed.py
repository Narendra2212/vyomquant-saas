"""API-02 -- unauthenticated test/debug endpoints removed from production.

THE DEFECT
    Two routes served a static "it's alive" response with no authentication and no purpose
    beyond manual poking during development:

    1. ``GET /api/test-basic`` (backend_app/main.py) -- "Test endpoint to verify basic
       routing works".
    2. ``GET /api/billing/test`` (backend_app/routers/billing.py) -- "Test endpoint to
       verify billing router is loaded".

    Neither is referenced by any test, monitoring check, or load balancer health probe
    (grepped the whole repo). They cost nothing to reach and prove nothing an operator
    couldn't already see from the real /health endpoints, while adding two more
    unauthenticated routes to the production attack surface.

THE FIX
    Both removed outright, per the finding's own remediation ("Remove.").

WHAT THIS FILE PROVES
    Both routes are gone from the real, fully-wired app -- not just deleted from a
    hand-built test client, but absent from ``app.routes`` on the actual FastAPI
    application main.py builds.
"""
from __future__ import annotations

from backend_app.main import app


def _registered_paths() -> set[str]:
    return {getattr(route, "path", None) for route in app.routes}


def test_the_root_test_basic_endpoint_is_gone() -> None:
    assert "/api/test-basic" not in _registered_paths()


def test_the_billing_test_endpoint_is_gone() -> None:
    assert "/api/billing/test" not in _registered_paths()


def test_the_real_billing_routes_around_it_are_unaffected() -> None:
    """The removal is surgical -- the routes immediately before/after it in the file are
    still registered."""
    paths = _registered_paths()
    assert "/api/billing/plans" in paths
    assert "/api/billing/entitlements" in paths
