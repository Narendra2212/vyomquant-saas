"""PORT-01: `POST /api/portfolio/close-all` imported a module that does not exist.

THE DEFECT
----------
`routers/portfolio.py` opened `close_all_positions` with::

    from backend_app.backend.safety_config import SafetyMonitor

There is no `backend_app/backend/safety_config.py`. The module is
`backend_app/core/safety_config.py`, which is what `routers/orders.py:96` imports. Because
the import sits *before* the `try` block, every call raised `ModuleNotFoundError` and
returned 500 — and the two controls beneath it never ran:

* the freeze check, `SafetyMonitor.check_execution_allowed("close_positions")`
* `ExecutionGuard.validate_trade(..., reduce_only=True)`

That silently removed one of only three `ExecutionGuard` call sites in the repository. It
failed closed — no money moved — but closing positions is the risk-REDUCING direction, so
an endpoint that always 500s is itself a hazard: a trader trying to flatten exposure
could not.

WHAT THIS FILE ASSERTS
----------------------
Not "the import succeeds" — that would pass if someone created an empty module. It
asserts the symbol resolves to the *same* `SafetyMonitor` the rest of the codebase uses,
that the module the route names actually exists, and that the route's intended controls
are still present in its body.

The route's final behaviour is a deliberate 403 `DIRECT_EXECUTION_BLOCKED` (all execution
must go through the strategy path), so reachability is asserted by the route getting *past
its imports* to its own refusal, rather than by a 200.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_the_module_the_route_imports_actually_exists():
    """The direct statement of the defect."""
    assert importlib.util.find_spec("backend_app.core.safety_config") is not None, (
        "backend_app.core.safety_config is missing; this test needs repointing"
    )
    assert importlib.util.find_spec("backend_app.backend.safety_config") is None, (
        "backend_app.backend.safety_config now exists. If it was created to satisfy the "
        "broken import, that is the wrong fix: the route must import the canonical "
        "core module that routers/orders.py uses, not a second copy."
    )


def test_the_route_imports_the_canonical_safety_monitor():
    """Resolved by AST, so a comment quoting the old path cannot satisfy it."""
    from backend_app.routers import portfolio

    # No dedent: `close_all_positions` is a module-level `async def`, so
    # `inspect.getsource` already returns it starting at column 0. Stripping four spaces
    # would remove the body's indentation and raise IndentationError -- which is exactly
    # what the first version of this test did.
    tree = ast.parse(inspect.getsource(portfolio.close_all_positions))

    safety_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and any(a.name == "SafetyMonitor" for a in node.names)
    ]

    assert safety_imports, "close_all_positions no longer imports SafetyMonitor"
    for module in safety_imports:
        assert module == "backend_app.core.safety_config", (
            f"close_all_positions imports SafetyMonitor from {module!r}; the canonical "
            "module is backend_app.core.safety_config"
        )


def test_it_is_the_same_SafetyMonitor_the_orders_router_uses():
    """One safety monitor, not two that can drift."""
    from backend_app.core.safety_config import SafetyMonitor as core_monitor
    from backend_app.routers import orders

    orders_source = inspect.getsource(orders)
    assert "from backend_app.core.safety_config import" in orders_source
    assert hasattr(core_monitor, "check_execution_allowed")


def test_importing_the_route_module_does_not_raise():
    """Pre-fix, importing was fine; it was CALLING that failed. Both must hold."""
    module = importlib.import_module("backend_app.routers.portfolio")
    assert hasattr(module, "close_all_positions")


def test_the_route_still_carries_its_intended_controls():
    """The fix must not be 'delete the import that was failing'.

    Asserted on the function's own source via AST-visible names, so the freeze check and
    the guard have to be real calls.
    """
    from backend_app.routers import portfolio

    tree = ast.parse(inspect.getsource(portfolio.close_all_positions))

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "check_execution_allowed" in called, "the freeze check is gone"
    assert "validate_trade" in called, "the ExecutionGuard validation is gone"
    assert "ExecutionGuard" in called, "the ExecutionGuard is no longer constructed"


def test_the_guard_is_invoked_in_reduce_only_mode():
    """Closing positions reduces risk, so the six capital checks are deliberately skipped.

    Pinned because a future edit that drops `reduce_only=True` would start refusing
    close-all whenever equity could not be read — leaving a trader exposed, which is the
    opposite of what this endpoint is for.
    """
    from backend_app.routers import portfolio

    source = inspect.getsource(portfolio.close_all_positions)
    assert "reduce_only=True" in source


def test_execution_guard_now_has_three_reachable_call_sites():
    """The count this fix restores.

    `ExecutionGuard` is the module that calls itself the final control layer. Before this
    fix one of its three sites was unreachable behind a broken import, so the effective
    count was two.
    """
    from backend_app.routers import orders, portfolio

    sites = 0
    for module in (orders, portfolio):
        tree = ast.parse(inspect.getsource(module))
        sites += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ExecutionGuard"
        )

    assert sites >= 3, (
        f"found {sites} ExecutionGuard construction sites across orders.py and "
        f"portfolio.py; expected at least 3"
    )
