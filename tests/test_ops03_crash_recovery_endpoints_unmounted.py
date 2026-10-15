"""OPS-03 -- the crash-recovery HTTP surface is no longer mounted.

THE DEFECT
    `routers/strategy_operations.py` exposed four crash-recovery endpoints
    (/crash-recovery/recover-deployment, recover-signals, verify-state, log) that called
    straight into `backend_app.core.crash_recovery.CrashRecoveryManager`. Every method
    that was supposed to touch real state there is a stub: `_get_deployment_state`
    returns a hardcoded placeholder for any deployment_id instead of querying the
    database, `_cancel_open_orders`/`_restart_worker` are bare `pass`, `_reconcile_
    portfolio` always returns the same fixed `{"reconciled": True}`, and `_verify_
    recovery` always returns `{"success": True, ...}`. So `recover_deployment` ALWAYS
    reported RecoveryStatus.SUCCEEDED, having cancelled no order, reconciled no
    position, and restarted nothing -- a fabricated safety confirmation on a crash-
    handling endpoint for a live-trading platform. `get_recovery_log` additionally
    returned every tenant's `active_recoveries`/`recovery_log`, unscoped by the
    caller's own user_id.

THE FIX
    The four route handlers and their now-unused `crash_recovery` imports were removed
    from `routers/strategy_operations.py`. `backend_app/core/crash_recovery.py` itself
    is UNCHANGED (its placeholder nature is now documented in its own module
    docstring) -- this is a mount removal, matching the precedent this session already
    set for RT-11's distributed_execution router, not a deletion.

WHAT THIS FILE PROVES
    * no route under /crash-recovery exists in the mounted app, for any method;
    * the strategy_operations router still mounts normally and still serves an
      unrelated existing route, so this was a scoped removal of four handlers, not a
      break of the whole router;
    * crash_recovery.py's module itself still exists with its stub methods intact --
      confirming this was a mount removal, not a deletion;
    * strategy_operations.py's source no longer imports anything from
      backend_app.core.crash_recovery.
"""
from __future__ import annotations

import inspect
import os

os.environ.setdefault("DEV_MODE", "true")

from backend_app.main import app
from backend_app.core import crash_recovery as crash_recovery_module
from backend_app.routers import strategy_operations as strategy_operations_module


def test_no_route_under_crash_recovery_is_mounted():
    matches = [
        (getattr(r, "methods", None), r.path)
        for r in app.routes
        if getattr(r, "path", None) and "crash-recovery" in r.path
    ]
    assert matches == [], f"still mounted: {matches}"


def test_strategy_operations_router_still_mounts_other_routes():
    # A scoped removal of four handlers, not an accidental break of the whole router:
    # dozens of other routes this module defines (pipeline, verification, etc.) must
    # still be present on the router object main.py mounts.
    other_routes = [
        r for r in strategy_operations_module.router.routes
        if "/crash-recovery" not in getattr(r, "path", "")
    ]
    assert len(other_routes) > 10


def test_the_crash_recovery_module_itself_still_exists_with_its_stub_methods():
    """Negative control: this is a mount removal, not a deletion."""
    assert hasattr(crash_recovery_module, "crash_recovery_manager")
    assert hasattr(crash_recovery_module.crash_recovery_manager, "recover_deployment")
    assert hasattr(crash_recovery_module, "verify_state_consistency")


def test_strategy_operations_no_longer_imports_crash_recovery():
    source = inspect.getsource(strategy_operations_module)
    assert "crash_recovery_manager" not in source
    assert "from backend_app.core.crash_recovery import" not in source


def test_recover_deployment_is_still_fabricated_in_the_unmounted_module():
    """Pinned so a future session re-mounting this module notices it must fix the
    underlying stub first, not just restore the route."""
    import asyncio

    async def _run():
        return await crash_recovery_module.crash_recovery_manager.recover_deployment(
            deployment_id="does-not-exist-anywhere",
            user_id="some-user",
            strategy_id="some-strategy",
        )

    result = asyncio.run(_run())
    assert result["status"] == "succeeded", (
        "if this no longer reports fabricated success, the module has been fixed -- "
        "update this test and reconsider re-mounting the endpoints instead of leaving "
        "them unmounted"
    )
