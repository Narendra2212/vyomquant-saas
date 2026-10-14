"""API-01 -- no two routes on the real app ever resolve the same (method, path) pair.

THE DEFECT
    ``routers/strategy_operations.py`` declared eleven (method, path) pairs that
    ``routers/strategies.py`` (or, for two of them, ``strategy_operations.py`` itself)
    already registered. ``main.py`` mounts ``strategies.router`` BEFORE
    ``strategy_operations.router``, and Starlette matches in registration order, so every
    one of these eleven handlers was permanently unreachable -- dead code that looked
    live (it had its own tests, its own docstrings, its own error codes) but could never
    actually answer a single request in any environment this has ever run in:

        GET    /api/strategies                        (list_strategies)
        GET    /api/strategies/{id}                    (get_strategy)
        PUT    /api/strategies/{id}                    (update_strategy)
        DELETE /api/strategies/{id}                    (delete_strategy)
        POST   /api/strategies/{id}/clone              (clone_strategy)
        POST   /api/strategies/{id}/deploy             (deploy_strategy)
        POST   /api/strategies/{id}/pause              (pause_strategy)
        POST   /api/strategies/{id}/stop               (pause_strategy, same handler)
        POST   /api/strategies/{id}/resume              (resume_strategy)
        POST   /api/deployments/{id}/stop              (stop_deployment_alias)
        DELETE /api/backtests/{id}                     (delete_backtest, 2nd copy)

THE FIX
    All eleven shadowed handlers (and the three now-unused Pydantic request models that
    were their only callers) were deleted. Nothing served by the app changed: a dead
    handler answered nothing before this change and the live handler at the same path
    answers identically after it.

WHAT THIS FILE PROVES
    A full, unconditional sweep of ``app.routes`` -- not a list of the eleven known
    offenders, which would pass trivially forever even if a twelfth collision were
    introduced tomorrow. Every (method, path) pair on the real app must resolve to
    exactly one endpoint function; a reader who reintroduces a shadowed duplicate
    anywhere in the app fails this file immediately, regardless of which router it is in.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from backend_app.main import app


def _route_table() -> Dict[Tuple[str, str], List[object]]:
    table: Dict[Tuple[str, str], List[object]] = defaultdict(list)
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if methods is None or path is None or endpoint is None:
            continue
        for method in methods:
            table[(method, path)].append(endpoint)
    return table


def test_no_method_path_pair_is_registered_by_more_than_one_endpoint() -> None:
    table = _route_table()
    collisions = {key: endpoints for key, endpoints in table.items() if len(endpoints) > 1}

    assert not collisions, (
        "the following (method, path) pairs are registered by more than one endpoint "
        "function. First-match-wins means every registration after the first is dead "
        "code that can never answer a request:\n"
        + "\n".join(
            f"  {method} {path} -> "
            + ", ".join(f"{ep.__module__}:{ep.__name__}" for ep in endpoints)
            for (method, path), endpoints in sorted(collisions.items())
        )
    )


def test_the_eleven_known_shadowed_handlers_are_actually_gone() -> None:
    """A regression-specific pin: each deleted name no longer exists at module level.

    Narrower than the sweep above (which would catch a REINTRODUCED duplicate under any
    name), but this additionally proves the specific dead functions were deleted rather
    than merely renamed or re-decorated at a non-colliding path, which the sweep alone
    would not distinguish.
    """
    import backend_app.routers.strategy_operations as ops

    deleted_names = (
        "list_strategies",
        "get_strategy",
        "update_strategy",
        "delete_strategy",
        "clone_strategy",
        "deploy_strategy",
        "pause_strategy",
        "resume_strategy",
        "stop_deployment_alias",
    )
    still_present = [name for name in deleted_names if hasattr(ops, name)]
    assert not still_present, (
        f"these handlers were supposed to be deleted from strategy_operations.py as "
        f"permanently shadowed dead code, but still exist: {still_present}"
    )

    # `delete_backtest` is special: ONE copy survives (the live, first-registered one) and
    # only the second, dead copy was deleted. The module attribute is necessarily the
    # surviving function; what matters is that only one ROUTE exists for its path, which
    # the sweep above already proves.
    assert hasattr(ops, "delete_backtest"), (
        "strategy_operations.delete_backtest should still exist — only the SECOND, dead "
        "registration of DELETE /backtests/{id} was removed, not the live one"
    )


def test_the_now_unused_request_models_are_gone_too() -> None:
    """The three Pydantic request models whose only callers were the deleted handlers."""
    import backend_app.routers.strategy_operations as ops

    for name in ("StrategyUpdateRequest", "StrategyDeployRequest", "StrategyCloneRequest"):
        assert not hasattr(ops, name), (
            f"{name} was only ever constructed by a now-deleted shadowed handler; leaving "
            "it defined would be dead code with nothing left to exercise it"
        )


def test_the_surviving_handlers_still_answer_their_paths() -> None:
    """NEGATIVE CONTROL. Deleting the dead duplicates must not have deleted the live ones."""
    table = _route_table()

    expectations = (
        ("GET", "/api/strategies", "backend_app.routers.strategies", "list_strategies"),
        ("GET", "/api/strategies/{strategy_id}", "backend_app.routers.strategies", "get_strategy_route"),
        ("PUT", "/api/strategies/{strategy_id}", "backend_app.routers.strategies", "update_strategy"),
        ("DELETE", "/api/strategies/{strategy_id}", "backend_app.routers.strategies", "delete_strategy"),
        ("POST", "/api/strategies/{strategy_id}/clone", "backend_app.routers.strategies", "clone_strategy"),
        ("POST", "/api/strategies/{strategy_id}/deploy", "backend_app.routers.strategies", "deploy_bot"),
        ("POST", "/api/strategies/{strategy_id}/stop", "backend_app.routers.strategies", "stop_bot"),
        ("POST", "/api/strategies/{strategy_id}/pause", "backend_app.routers.strategies", "pause_strategy"),
        ("POST", "/api/strategies/{strategy_id}/resume", "backend_app.routers.strategies", "resume_strategy"),
        (
            "POST",
            "/api/deployments/{deployment_id}/stop",
            "backend_app.routers.strategy_operations",
            "stop_deployment",
        ),
        (
            "DELETE",
            "/api/backtests/{backtest_id}",
            "backend_app.routers.strategy_operations",
            "delete_backtest",
        ),
    )
    for method, path, module, handler in expectations:
        endpoints = table.get((method, path))
        assert endpoints, f"{method} {path} resolves to NO endpoint at all"
        assert len(endpoints) == 1, f"{method} {path} is still registered more than once"
        served = endpoints[0]
        served_name = f"{served.__module__}:{served.__name__}"
        assert served_name == f"{module}:{handler}", (
            f"{method} {path} is served by {served_name}, expected {module}:{handler}"
        )
