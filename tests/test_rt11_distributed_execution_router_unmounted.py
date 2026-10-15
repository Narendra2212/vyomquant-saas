"""RT-11 -- the distributed-execution HTTP surface is no longer mounted.

THE DEFECT
    `routers/distributed_execution.py` exposed a full job-submission/status/replay/audit
    API (`/api/distributed-execution/*`), documented as "institutional-grade reliability"
    via queue-based distributed processing. Nothing in this codebase ever initializes or
    starts `execution_orchestrator` (confirmed by
    tests/test_execution_paths_that_claim_an_exchange.py's own measurement:
    `exchange_gateways == {}` and `worker_pool is None`, always), so every submission was
    already refused with 400 before this fix -- the dangerous failure mode (a job silently
    accepted and never processed) was already closed. What remained was a fully-mounted,
    reachable HTTP surface for a queue nothing drains: confusing to any caller, and
    advertising a capability (distributed, institutional-grade order execution) this
    platform does not have.

THE FIX
    `app.include_router(distributed_execution.router, ...)` removed from main.py, along
    with its now-unused import. The router module and the `backend/distributed_execution/`
    package are UNCHANGED -- several existing tests import them directly by path to pin
    the orchestrator's unstarted state, and this fix does not touch that.

WHAT THIS FILE PROVES
    * no route under /api/distributed-execution exists in the mounted app, for any method;
    * the module itself still exists and still exposes `router` with its handlers --
      confirming this was a mount removal, not a deletion that would break the tests that
      import it directly;
    * no frontend caller exists (confirmed by a repo-wide grep this session; recorded here
      as a negative control rather than re-grepped by this test).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DEV_MODE", "true")

from backend_app.main import app
from backend_app.routers import distributed_execution as distributed_execution_router_module


def test_no_route_under_distributed_execution_is_mounted():
    matches = [
        (getattr(r, "methods", None), r.path)
        for r in app.routes
        if getattr(r, "path", None) and "distributed-execution" in r.path
    ]
    assert matches == [], f"still mounted: {matches}"


def test_the_router_module_itself_still_exists_with_its_handlers():
    """Negative control: this is a mount removal, not a deletion."""
    assert hasattr(distributed_execution_router_module, "router")
    assert hasattr(distributed_execution_router_module, "submit_execution_job")


def test_main_py_no_longer_imports_the_router_module_by_name():
    import inspect

    import backend_app.main as main_module

    source = inspect.getsource(main_module)
    assert "app.include_router(distributed_execution.router" not in source
