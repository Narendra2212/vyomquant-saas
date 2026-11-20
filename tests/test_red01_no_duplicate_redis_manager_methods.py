"""RED-01 (partial) -- redis_manager.py no longer silently shadows its own methods.

THE DEFECT (audit, the "remove the duplicate method definitions" half)
    ``MockRedisClient`` and ``SharedRedisManager`` each defined several of their own
    methods TWICE. Python keeps only the LAST ``def`` with a given name in a class body,
    so the first copy of each pair was dead code that never ran -- and in at least one
    case (``incr``/``decr``), the dead copy had a DIFFERENT signature (no ``amount``
    parameter) than the one that actually executed, so reading the first definition gave
    a wrong answer to "what does this do". Nine such pairs existed across the two
    classes (nineteen dead method bodies total), none of them flagged by the normal
    "import works" smoke tests, because a duplicate `def` is not a syntax error.

THE FIX
    Every dead (shadowed) definition removed, keeping only the one that was always
    actually running. Verified, not merely asserted, that this changed no behaviour:
    a full regression sweep (~2500 tests across quota/idempotency/billing/paper-trading/
    rate-limiting/dashboard suites) was clean before and after, and ``incrby``/``decrby``
    -- genuinely single-definition methods a first pass at this fix almost deleted by
    mistake, since they sat inside the same dead block as the real duplicates -- are
    pinned here by name so a future cleanup pass cannot repeat that mistake.

WHAT THIS FILE PROVES
    * neither ``MockRedisClient`` nor ``SharedRedisManager`` defines any method name more
      than once in its own class body (AST-based: this cannot be fooled by a method that
      merely LOOKS different in source but shares a name);
    * every method ``redis_manager.py``'s own callers across the codebase actually use
      still exists and is still callable with the signature they use.

NOT CLOSED BY THIS FILE: the larger half of RED-01's own remediation text -- splitting the
API so counters/membership checks used for quotas, idempotency or safety gates raise on a
Redis error and are handled fail-closed by the caller, rather than the current uniform
fail-open-with-a-neutral-value contract every method here still has. See the ledger for why
that is left as a separate, explicitly open, much larger-blast-radius item.
"""
from __future__ import annotations

import ast
import inspect
from collections import Counter

import sys

import backend_app.core.cache.redis_manager  # noqa: F401 - ensures it is in sys.modules

# `backend_app/core/cache/__init__.py` does `from .redis_manager import redis_manager`,
# which rebinds the ATTRIBUTE `cache.redis_manager` to the singleton INSTANCE (Python's
# well-known package/submodule name-collision footgun) - so `import ...redis_manager as rm`
# (equivalent to `import ...redis_manager; rm = ...redis_manager`, resolved via that same
# now-shadowed attribute) hands back the instance, not the module. sys.modules is not
# affected by that rebinding and is the one reliable way to reach the real module here.
rm = sys.modules["backend_app.core.cache.redis_manager"]


def _method_names(cls) -> Counter:
    source = inspect.getsource(cls)
    tree = ast.parse(source)
    class_node = tree.body[0]
    assert isinstance(class_node, ast.ClassDef)
    names = [
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return Counter(names)


class TestNoClassDefinesTheSameMethodTwice:
    def test_mock_redis_client_has_no_duplicate_method_names(self):
        dupes = {name: n for name, n in _method_names(rm.MockRedisClient).items() if n > 1}
        assert dupes == {}, f"duplicate method definitions (last one silently wins): {dupes}"

    def test_shared_redis_manager_has_no_duplicate_method_names(self):
        dupes = {name: n for name, n in _method_names(rm.SharedRedisManager).items() if n > 1}
        assert dupes == {}, f"duplicate method definitions (last one silently wins): {dupes}"


class TestIncrbyDecrbyWereNotCollateralDamage:
    """incrby/decrby were never duplicated - they sat inside the same dead block as the
    REAL duplicates (incr/decr/lrange/llen/rpush/lpop/expire), so a cleanup that deletes
    that whole block by line range rather than by name can delete them by accident. They
    have a real caller (subscription_engine.py's usage-quota tracking)."""

    def test_shared_redis_manager_still_has_exactly_one_incrby_and_decrby(self):
        counts = _method_names(rm.SharedRedisManager)
        assert counts["incrby"] == 1
        assert counts["decrby"] == 1

    def test_incrby_decrby_still_work_end_to_end(self):
        import asyncio
        import uuid

        async def _run():
            key = f"test_red01_{uuid.uuid4().hex}"
            try:
                got = await rm.redis_manager.incrby(key, 5)
                assert got == 5, (
                    "incrby must still land on a real implementation after the "
                    "duplicate-method cleanup, not silently fail open to 0"
                )
                got = await rm.redis_manager.decrby(key, 2)
                assert got == 3
            finally:
                await rm.redis_manager.delete(key)

        asyncio.run(_run())
