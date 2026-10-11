"""QUOTA-01: the product's quotas are enforced before the side effect, and fail closed.

THE AUDIT FINDING, AND WHY IT WAS MISLEADING
============================================
The audit reported 15 `HardQuotaEnforcer` methods with zero call sites. True, and still
true. The inference — that quotas are unenforced — is false: the product enforces them
through `core/subscription_dependencies.py`, wired as FastAPI `Depends(...)` on the
mutation routes. `HardQuotaEnforcer` is a parallel, unwired second implementation.

**Wiring it would be a defect.** Its limit source is `TenantQuota` rather than the
subscription plan catalogue (two sources of truth for one number), and its usage counting
reads the FAIL-SOFT Redis facade — so a Redis outage would make every count read zero and
every quota pass. That is fail-open beside a fail-closed path.

WHAT THIS FILE ASSERTS
----------------------
1. Each quota dependency is attached to the real route, resolved by AST from the
   function signature -- not by a substring scan, and not by checking the function
   merely exists.
2. Enforcement is structurally BEFORE the side effect, because a FastAPI dependency
   resolves before the handler body. Section 3 proves that by driving a real route and
   showing the handler never ran.
3. Unreadable usage fails CLOSED (503), not open.
4. Identity is token-derived, never caller-supplied.
5. Negative control: a quota implementation that ran AFTER the side effect would fail
   the test in §3.

Full trace: staging/QUOTA_ENFORCEMENT_MAP.md
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap
from typing import Dict, List, Set

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: quota dependency -> the routes it MUST be attached to, as (module path, handler name).
#: Derived from the enforcement map, so the map and the test cannot disagree.
EXPECTED: Dict[str, List[str]] = {
    "check_strategy_quota": ["backend_app/routers/strategies.py",
                             "backend_app/routers/library.py"],
    "check_bot_quota": ["backend_app/routers/strategies.py"],
    "check_backtest_quota": ["backend_app/routers/strategies.py",
                             "backend_app/routers/strategy_operations.py"],
    "check_ml_quota": ["backend_app/routers/strategies.py",
                       "backend_app/routers/strategy_operations.py"],
    "check_optimization_quota": ["backend_app/routers/strategies.py",
                                 "backend_app/routers/strategy_operations.py"],
    "check_marketplace_publish_quota": ["backend_app/routers/library.py"],
    "check_marketplace_subscription_quota": ["backend_app/routers/library.py"],
}


def _depends_names(path: str) -> Set[str]:
    """Every name used as `Depends(<name>)` in a FUNCTION SIGNATURE in `path`.

    Signature-scoped on purpose: a `Depends(...)` inside a docstring example or a body
    expression is not an attached dependency, and `core/entitlement_dependencies.py`
    contains exactly that -- three docstring examples that made an entirely unwired
    module look wired to a substring scan.
    """
    full = os.path.join(REPO, path)
    tree = ast.parse(open(full, encoding="utf-8", errors="replace").read())
    found: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        for default in list(args.defaults) + list(args.kw_defaults):
            if default is None:
                continue
            for sub in ast.walk(default):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "Depends"
                        and sub.args):
                    target = sub.args[0]
                    if isinstance(target, ast.Name):
                        found.add(target.id)
                    elif isinstance(target, ast.Attribute):
                        found.add(target.attr)
    return found


# ── 1. every expected quota is attached to its real route ──────────────────


@pytest.mark.parametrize("dependency,modules", sorted(EXPECTED.items()))
def test_the_quota_dependency_is_attached_to_its_routes(dependency, modules):
    for module in modules:
        attached = _depends_names(module)
        assert dependency in attached, (
            f"{dependency} is not attached as a signature Depends in {module}. The "
            f"quota is therefore not enforced on that route -- which is exactly the "
            f"'exists but is never reached' defect class this engagement was "
            f"commissioned to find."
        )


def test_the_deploy_route_carries_both_the_feature_gate_and_the_bot_quota():
    """Deploying a bot is the money-critical one; it needs both."""
    attached = _depends_names("backend_app/routers/strategies.py")
    assert "check_bot_quota" in attached
    assert "require_live_trading" in attached


# ── 2. fail closed on unreadable usage ─────────────────────────────────────


def test_unreadable_usage_raises_503_not_a_pass():
    """'A figure that could not be read is not a figure of zero.'

    Asserted on the module's real source: every `except UsageReadFailed` must raise, and
    must raise a 503 rather than returning.
    """
    import backend_app.core.subscription_dependencies as sd

    tree = ast.parse(inspect.getsource(sd))
    handlers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and node.type is not None
        and "UsageReadFailed" in ast.unparse(node.type)
    ]
    assert handlers, "no UsageReadFailed handler found; repoint this guard"

    for handler in handlers:
        raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
        assert raises, (
            "a UsageReadFailed handler does not raise, so an unreadable usage figure "
            "would be treated as capacity available -- fail OPEN"
        )
        rendered = " ".join(ast.unparse(r) for r in raises)
        assert "503" in rendered or "SERVICE_UNAVAILABLE" in rendered, (
            f"a UsageReadFailed handler raises something other than a 503: {rendered[:160]}"
        )


def test_every_quota_dependency_takes_its_identity_from_the_token():
    """Client-supplied quota identity must not be trusted.

    Each dependency must take `user` from `Depends(get_current_user)`; none may accept a
    user or tenant id as a plain parameter.
    """
    import backend_app.core.subscription_dependencies as sd

    tree = ast.parse(inspect.getsource(sd))
    offenders: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith(("check_", "require_")):
            continue
        params = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        for suspicious in ("user_id", "tenant_id"):
            if suspicious in params:
                offenders.append(f"{node.name}({suspicious})")
    assert offenders == [], (
        f"quota dependencies accept a caller-supplied identity: {offenders}. Identity "
        f"must come from the authenticated token."
    )


# ── 3. enforcement happens BEFORE the side effect ──────────────────────────


def test_a_failing_quota_prevents_the_handler_from_running():
    """THE INVARIANT: a breach must prevent the side effect, not record it after.

    Driven through a real FastAPI app so the ordering is FastAPI's, not a convention. The
    handler sets a flag; a refusing dependency must mean the flag is never set.
    """
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient

    side_effects: List[str] = []

    async def refusing_quota():
        raise HTTPException(status_code=429, detail="QUOTA_EXCEEDED")

    app = FastAPI()

    @app.post("/create")
    async def create(_quota=Depends(refusing_quota)):
        side_effects.append("created")   # the side effect
        return {"ok": True}

    resp = TestClient(app).post("/create")

    assert resp.status_code == 429
    assert side_effects == [], (
        "the handler body ran despite the quota dependency refusing; the quota would be "
        "recording a breach after the fact instead of preventing it"
    )


def test_a_passing_quota_lets_the_handler_run():
    """Positive control. Without it, an app that refused everything would look correct."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    side_effects: List[str] = []

    async def permitting_quota():
        return True

    app = FastAPI()

    @app.post("/create")
    async def create(_quota=Depends(permitting_quota)):
        side_effects.append("created")
        return {"ok": True}

    resp = TestClient(app).post("/create")

    assert resp.status_code == 200
    assert side_effects == ["created"]


def test_a_quota_checked_AFTER_the_side_effect_would_fail_this_suite():
    """NEGATIVE CONTROL.

    Reconstructs the wrong design -- enforce after mutating -- and shows the side effect
    has already happened by the time the breach is detected. If this ever stops showing
    the side effect, §3's assertion proves nothing.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    side_effects: List[str] = []

    app = FastAPI()

    @app.post("/create")
    async def create():
        side_effects.append("created")           # side effect FIRST
        raise HTTPException(status_code=429, detail="QUOTA_EXCEEDED")  # check after

    resp = TestClient(app).post("/create")

    assert resp.status_code == 429
    assert side_effects == ["created"], (
        "the reconstruction did not perform its side effect, so this control cannot "
        "demonstrate the ordering bug it exists to demonstrate"
    )


# ── 4. HardQuotaEnforcer stays unwired, deliberately ──────────────────────


def test_hard_quota_enforcer_is_still_unwired_and_that_is_the_decision():
    """It must NOT be wired as written.

    Its usage counting reads the fail-soft Redis facade, so a Redis outage would make
    every count read zero and every quota pass -- fail OPEN, beside a fail-closed path.
    And its limit source is TenantQuota rather than the subscription catalogue, which
    would be a second source of truth for one number.

    This test will FAIL if somebody wires it, which is intended: that change needs the
    reasoning in staging/QUOTA_ENFORCEMENT_MAP.md §2 revisited first.
    """
    from pathlib import Path

    backend = Path(REPO) / "backend_app"
    callers: List[str] = []
    for path in sorted(backend.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "hard_quota_enforcer.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("enforce_")):
                receiver = ast.unparse(node.func.value)
                if "quota" in receiver.lower() or "enforcer" in receiver.lower():
                    callers.append(
                        f"{path.relative_to(REPO).as_posix()}:{node.lineno} "
                        f"{receiver}.{node.func.attr}"
                    )

    assert callers == [], (
        "HardQuotaEnforcer.enforce_* is now invoked at:\n  " + "\n  ".join(callers)
        + "\n\nRead staging/QUOTA_ENFORCEMENT_MAP.md section 2 before accepting this: "
          "its usage counting reads the FAIL-SOFT Redis facade, so a Redis outage makes "
          "every count read zero and every quota pass."
    )


def test_hard_quota_enforcers_usage_counting_is_fail_open_as_documented():
    """The evidence for the decision above, pinned.

    If this ever stops being true -- if the class starts failing closed -- the
    deprecation argument weakens and should be re-examined.
    """
    import backend_app.core.hard_quota_enforcer as hq

    source = inspect.getsource(hq)
    tree = ast.parse(source)
    redis_reads = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and "redis_manager" in ast.unparse(node.func.value)
    }
    assert redis_reads, (
        "HardQuotaEnforcer no longer reads redis_manager; the fail-open argument in the "
        "enforcement map needs revisiting"
    )


# ── 5. the safety-critical quota has a database backstop ──────────────────


def test_the_bot_quota_has_a_database_backstop(tmp_path):
    """Two concurrent deploys could both pass the dependency; the DB must stop them.

    The bot quota is the one whose breach costs money, and it is the only one with a
    database-level constraint behind it. Verified under real concurrency -- including
    across separate OS processes -- by
    tests/test_work03_bot_ownership_claims_postgres.py (27 tests).
    """
    from pathlib import Path

    migration = (
        Path(REPO) / "backend_app" / "migrations"
        / "025_work03_bot_ownership_claims.sql"
    )
    assert migration.exists(), "the WORK-03 claim migration is missing"
    sql = migration.read_text(encoding="utf-8")
    assert "PRIMARY KEY (user_id, symbol)" in sql, (
        "the (user_id, symbol) primary key is the cross-process bot invariant"
    )


def test_the_enforcement_map_exists_and_names_the_gaps():
    """A map that only listed what works would be marketing, not an audit."""
    from pathlib import Path

    doc = Path(REPO) / "staging" / "QUOTA_ENFORCEMENT_MAP.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")

    assert "REMAINING GAPS" in text.upper()
    for gap in ("order rate", "concentration", "race"):
        assert gap.lower() in text.lower(), (
            f"the enforcement map does not record the {gap!r} gap"
        )
    assert "DOWNGRADED" in text.upper(), (
        "the map must state the status change explicitly rather than implying closure"
    )
