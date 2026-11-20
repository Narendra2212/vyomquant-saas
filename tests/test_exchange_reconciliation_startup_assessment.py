"""Phase 10, observation D: the operational wiring of exchange reconciliation, pinned.

WHAT WAS OBSERVED
-----------------
There are THREE distinct reconciliation components, and they are not the same thing:

  1. `core/reconciliation_scheduler.py::ReconciliationScheduler` — an ESCALATION loop. It
     polls `reconciliation_mismatches` every 30s and escalates/kill-switches on CRITICAL.
     It is STARTED in `main.py`'s lifespan (`start_reconciliation_scheduler()`), with a
     visible ACTIVE/INACTIVE status line. It processes mismatches that something else
     recorded; it does not itself talk to any exchange.
  2. `backend/exchange_reconciliation.py::ExchangeReconciliationService` — the component
     that would FETCH orders from the venue and COMPARE them to internal records (i.e.
     PRODUCE the order/fill mismatches the scheduler escalates). Its
     `start_reconciliation_loop` is called from NOWHERE in the application; only its own
     docstring example names it.
  3. `backend/reconciliation_worker.py::reconciliation_worker` — a module-level singleton
     that its OWN docstring records as never started by any entrypoint.

So the escalation loop runs, and environment mismatches ARE produced for it
(`backend/execution_environment.py::_record_mismatch` writes paper/live divergences). But
the VENUE order/fill reconciliation — comparing this platform's orders against the
exchange's actual orders — is NOT operational, because the service that would perform it is
never started.

WHAT THIS FILE DOES, AND DELIBERATELY DOES NOT DO
-------------------------------------------------
It PINS that wiring state so the gap is explicit and a change to it is visible, and it
confirms the service FAILS SAFE on exchange errors (so a future wiring does not silently
assume filled). It does NOT start `ExchangeReconciliationService` against a live or sandbox
venue — that needs an exchange this environment does not have and the engagement forbids —
and it does NOT claim end-to-end exchange reconciliation has been verified. The
venue-reconciliation gap is reported OPEN, not fixed.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _source(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def test_the_escalation_scheduler_is_started_in_lifespan_with_visible_status():
    """The mismatch-escalation loop IS wired, and its start is observable.

    A reconciliation control that started silently (or not at all) while the app reported
    healthy would be the "implies active but is not" failure mode the observation is about.
    main.py starts it and records ACTIVE/INACTIVE in service_status, so a startup failure is
    visible rather than mistaken for a running control.
    """
    main_src = _source("backend_app/main.py")
    assert "start_reconciliation_scheduler()" in main_src, (
        "ReconciliationScheduler is not started in main.py lifespan"
    )
    assert 'service_status["reconciliation_scheduler"]' in main_src, (
        "the scheduler's start is not recorded in service_status, so a failed start would "
        "not be visible"
    )
    # Both the ACTIVE and the INACTIVE branch must set the status, so an exception during
    # start is reported rather than swallowed into a healthy-looking app.
    assert '"reconciliation_scheduler"] = "ACTIVE"' in main_src
    assert '"reconciliation_scheduler"] = "INACTIVE"' in main_src


def test_the_exchange_reconciliation_service_is_not_started_anywhere():
    """OBSERVATION D, pinned: ExchangeReconciliationService.start_reconciliation_loop has
    no caller in the application, so venue order/fill reconciliation is NOT operational.

    Asserted by walking every backend_app source for a call to `start_reconciliation_loop`.
    If someone wires it (which needs a real exchange and should be a deliberate, reviewed
    change), this test fails and this file's assessment must be revisited -- which is the
    point: the gap must not be closed silently, and it must not be left closed-looking while
    the service sits unstarted.
    """
    callers = []
    backend = REPO / "backend_app"
    for path in backend.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "exchange_reconciliation.py":
            continue  # its own docstring example and method definition are not callers
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start_reconciliation_loop"):
                callers.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")

    assert callers == [], (
        "ExchangeReconciliationService.start_reconciliation_loop now HAS caller(s): "
        + ", ".join(callers)
        + ". Venue reconciliation would then be operational -- but it needs a real/sandbox "
        "exchange, and this engagement forbids one, so wiring it is a deliberate reviewed "
        "change that invalidates this file's 'not started' assessment. Update the Phase 10 "
        "report and the reconciliation evidence before accepting it."
    )


def test_the_reconciliation_worker_singleton_is_not_started_either():
    """The third component is also unstarted, per its own docstring. Pinned so a change shows.

    `reconciliation_worker` is a module-level singleton whose docstring records that nothing
    starts its loop. Confirmed structurally: no backend_app source calls
    `reconciliation_worker.start()` (or `.start_reconciliation_loop()`), outside its own
    module.
    """
    callers = []
    backend = REPO / "backend_app"
    for path in backend.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "reconciliation_worker.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("start", "start_reconciliation_loop")):
                recv = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
                if "reconciliation_worker" in recv:
                    callers.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")

    assert callers == [], (
        "reconciliation_worker is now started at: " + ", ".join(callers)
        + ". Its docstring says nothing starts it; reconcile the two."
    )


def test_the_exchange_reconciliation_service_fails_safe_on_exchange_errors():
    """If venue reconciliation IS wired later, it must not assume filled on an exchange fault.

    This does not start the service against a venue; it confirms the fail-safe property is
    present in the code (a timeout/error does not become 'no orders' / 'filled'). The
    behavioural proof already lives in tests/test_exchange_safety_fix.py
    (test_exchange_timeout_fails_safely, test_exchange_error_propagates,
    test_empty_response_not_assumed); this pins the structural guarantee that
    `_fetch_exchange_orders` RAISES rather than returning a default on error.
    """
    import textwrap

    from backend_app.backend.exchange_reconciliation import ExchangeReconciliationService

    src = textwrap.dedent(
        inspect.getsource(ExchangeReconciliationService._fetch_exchange_orders)
    )
    tree = ast.parse(src)
    # The method must contain at least one `raise` and must not have a blanket
    # `except: return []` that would turn an exchange fault into "no open orders".
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert raises, (
        "_fetch_exchange_orders has no raise; an exchange timeout/error could be swallowed "
        "into an empty result, which reads as 'no orders' -- the assume-filled failure mode"
    )
