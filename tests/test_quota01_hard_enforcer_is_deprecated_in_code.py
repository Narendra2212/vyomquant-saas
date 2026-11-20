"""QUOTA-01: the decision not to wire ``HardQuotaEnforcer`` is now expressed in code.

WHY THIS FILE EXISTS
--------------------
Phase 4 decided that ``core/hard_quota_enforcer.py`` must NOT be wired, and gave three
reasons (its limits come from ``TenantQuota`` rather than the subscription catalogue; its
tenant model is the vestigial one TENANT-01 documents; and decisively, its usage counting
reads the fail-SOFT ``redis_manager`` facade, so a cache outage makes every count read zero
and every limit admit). That decision lived only in ``staging/QUOTA_ENFORCEMENT_MAP.md``.

A decision recorded only in a report is a decision the next engineer will not see. The audit
listed "HardQuotaEnforcer has not actually been deprecated in code" as an open residual, and
``tests/test_quota01_enforcement_is_wired.py`` could only assert the ABSENCE of call sites —
which is indistinguishable from "nobody has got round to it yet".

So the deprecation is now in the module: a docstring that states the reasoning, a
``DeprecationWarning`` on construction naming it, a lazily-built singleton so merely importing
the module is not itself deprecated use, and the removal of the two attributes on live services
that constructed the class and never read it. This file pins all of that.

THE FAIL-OPEN MECHANISM IS SUBTLE AND IS PINNED DELIBERATELY
------------------------------------------------------------
Read in isolation, ``_get_current_count`` and ``_increment_count`` look fail-CLOSED: each
raises ``RuntimeError`` in production when Redis misbehaves. Those branches never execute. The
facade swallows first — ``redis_manager.get()`` catches every exception and returns ``None``,
``incr()`` catches and returns ``0`` — so no exception ever reaches this module and every count
reads zero. Anyone re-reading this file to re-open the decision will see the fail-closed
``except`` blocks and reasonably conclude the audit was wrong about it. The test below encodes
which of the two readings is correct.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "backend_app" / "core" / "hard_quota_enforcer.py"


def test_constructing_the_enforcer_warns_that_it_is_deprecated():
    """The warning is the part a developer actually meets. It must fire, and it must explain.

    A bare ``DeprecationWarning`` with no reasoning would send the reader to the class, where
    the fail-closed-looking ``except`` blocks would mislead them. So the message has to name the
    mechanism and point at the path that does work.
    """
    from backend_app.core.hard_quota_enforcer import (DEPRECATION_MESSAGE,
                                                      HardQuotaEnforcer)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        HardQuotaEnforcer()

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, (
        "constructing HardQuotaEnforcer emitted no DeprecationWarning, so an attempt to wire "
        "it surfaces in an audit rather than at the first test run"
    )
    message = str(deprecations[0].message)
    assert message == DEPRECATION_MESSAGE

    lowered = message.lower()
    # It names the replacement...
    assert "subscription_dependencies" in lowered, (
        "the warning does not say what to use instead"
    )
    # ...and the actual reason, so the reader does not have to rediscover it.
    for phrase in ("fail", "redis", "tenantquota"):
        assert phrase in lowered, f"the warning does not mention {phrase!r}"


def test_importing_the_module_does_not_itself_warn():
    """A warning that fires when nobody is doing anything wrong gets filtered out.

    The singleton used to be built at module scope, so with a warning on ``__init__`` the mere
    act of importing — including importing only the error types, or a test reading the
    docstring — would have been reported as deprecated use. It is lazy now.
    """
    import importlib
    import sys

    sys.modules.pop("backend_app.core.hard_quota_enforcer", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module("backend_app.core.hard_quota_enforcer")

    deprecations = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "HardQuotaEnforcer" in str(w.message)
    ]
    assert deprecations == [], (
        "importing hard_quota_enforcer emitted a deprecation warning; the singleton is being "
        "constructed at import time again"
    )

    # The name still resolves, so nothing importing it breaks — it is just built on demand,
    # and THAT is the deprecated use the warning is for.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        singleton = module.hard_quota_enforcer
    assert singleton is not None
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        "touching the singleton did not warn"
    )
    # And it is a singleton: the second access does not build a second one.
    assert module.hard_quota_enforcer is singleton


def test_the_module_docstring_states_the_decision_and_its_reasoning():
    """The long-form reasoning belongs next to the code, not only in staging/.

    Asserted on content rather than wording so the prose can be improved, but the four load-
    bearing claims cannot quietly disappear.
    """
    src = MODULE.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    docstring = ast.get_docstring(tree) or ""
    assert docstring, "hard_quota_enforcer.py has no module docstring"

    lowered = docstring.lower()
    assert "deprecated" in lowered and "do not wire" in lowered, (
        "the docstring does not say the module is deprecated and must not be wired"
    )
    # 1. The path that DOES enforce.
    assert "subscription_dependencies" in lowered
    # 2. The second source of truth.
    assert "tenantquota" in lowered
    # 3. The decisive reason, including WHERE the swallowing happens — without that pointer the
    #    fail-closed-looking except blocks below read as a contradiction.
    assert "redis_manager" in lowered
    assert "fail-open" in lowered or "fail open" in lowered
    # 4. Why the file is kept rather than deleted.
    assert "orders per minute" in lowered or "order rate" in lowered


def test_no_live_service_constructs_the_deprecated_enforcer():
    """The two dormant instances are gone, and may not come back.

    ``core/dag_task_queue.py`` and ``core/entitlement_engine.py`` each constructed the class and
    never read the attribute. An unused instance of a fail-open enforcer sitting on a live
    service is an invitation to start calling it, which is how the second source of truth would
    arrive — not by a decision, but by convenience.
    """
    offenders = []
    for path in sorted((REPO / "backend_app").rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "hard_quota_enforcer.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HardQuotaEnforcer"
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")

    assert offenders == [], (
        "HardQuotaEnforcer is constructed at:\n  " + "\n  ".join(offenders)
        + "\n\nIt is deprecated: its usage counting reads the fail-soft Redis facade, so a "
          "cache outage makes every count read zero and every limit admit. Read its module "
          "docstring before accepting this."
    )


def test_the_fail_open_mechanism_is_still_what_the_code_does():
    """The evidence for the decision, pinned at both ends.

    Two halves, and the test is only meaningful with both: this module must still READ its
    usage through the facade, and the facade must still SWALLOW. If either changes — if the
    enforcer grew a direct client, or the facade started propagating — the decision needs
    revisiting rather than inheriting.
    """
    src = MODULE.read_text(encoding="utf-8", errors="replace")
    assert "redis_manager" in src, (
        "hard_quota_enforcer no longer reads redis_manager; its fail-open premise has changed "
        "and the deprecation reasoning needs re-deriving"
    )

    facade = (REPO / "backend_app" / "core" / "cache" / "redis_manager.py").read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(facade)

    def _swallows(method_name: str) -> bool:
        """Whether ``RedisManager.<method>`` catches and returns instead of re-raising."""
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name:
                for handler in [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]:
                    body = handler.body
                    raises = any(isinstance(stmt, ast.Raise) for stmt in ast.walk(handler))
                    if not raises and body:
                        return True
        return False

    assert _swallows("get"), (
        "redis_manager.get no longer swallows exceptions. The enforcer's own fail-CLOSED "
        "except blocks might now actually fire, which would change the QUOTA-01 decision."
    )
    assert _swallows("incr"), "redis_manager.incr no longer swallows exceptions."


def test_the_enforced_path_is_the_one_that_fails_closed():
    """The contrast that justifies the deprecation, asserted rather than asserted-about.

    The wired path refuses when it cannot read usage. Shown by behaviour, so this cannot pass
    on a comment: an ``UsageReadFailed`` from the ledger must become a 503, never an admission.
    """
    import asyncio

    from fastapi import HTTPException

    from backend_app.core import subscription_dependencies as sd
    from backend_app.core import usage_ledger
    from backend_app.core.subscription_engine import Resource
    from backend_app.core.usage_ledger import UsageReadFailed

    async def _exercise():
        async def _plan_context(user_id, supabase):
            return sd.PlanContext(plan="free", tier="free", display_name="Free")

        async def _unreadable(*_a, **_k):
            raise UsageReadFailed("the strategies count could not be established")

        original_ctx, original_count = sd.get_plan_context, usage_ledger.count
        sd.get_plan_context = _plan_context
        usage_ledger.count = _unreadable
        sd.usage_ledger.count = _unreadable
        try:
            with pytest.raises(HTTPException) as excinfo:
                await sd.enforce_quota(
                    Resource.STRATEGIES.value, {"id": "u"}, object()
                )
            return excinfo.value
        finally:
            sd.get_plan_context = original_ctx
            usage_ledger.count = original_count
            sd.usage_ledger.count = original_count

    exc = asyncio.run(_exercise())
    assert exc.status_code == 503, (
        f"an unreadable usage figure produced {exc.status_code}, not a 503 refusal. A figure "
        f"that could not be read is not a figure of zero."
    )
    assert exc.detail["error"] == "ENTITLEMENT_USAGE_UNREADABLE"


def test_the_removed_attributes_have_no_remaining_readers():
    """Removing the attributes must not have left a reader behind.

    ``self.enforcer`` and ``self.quota_enforcer`` were unused, which is why removing them was
    safe — but "unused" was a measurement, and this is that measurement as a test, so the two
    files cannot regain a reader of an attribute that no longer exists and fail at runtime
    instead of here.
    """
    for relative in ("core/dag_task_queue.py", "core/entitlement_engine.py"):
        path = REPO / "backend_app" / relative
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        readers = [
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in ("enforcer", "quota_enforcer")
        ]
        assert readers == [], (
            f"{relative} still references a removed enforcer attribute at {readers}; it would "
            f"raise AttributeError at runtime"
        )
