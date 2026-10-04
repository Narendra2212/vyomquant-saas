"""
tests/test_idempotency_lua_prefix_agreement.py

The atomic check-and-set Lua script must classify an in-flight lock as a lock.

WHAT WAS WRONG
--------------
``DistributedIdempotency._atomic_check_and_set`` writes its lock as
``f"processing:{owner_token}"`` and its Lua script decided "is this a finished result or a
lock held by someone else?" like this::

    if current_value and string.sub(current_value, 1, 7) ~= 'processing' then
        return {1, current_value}   -- treat as a cached result
    end

``string.sub(v, 1, 7)`` is ``"process"``. The literal it is compared against is
``"processing"`` — ten characters. ``"process" ~= "processing"`` is therefore **true for any
non-nil value**, including a live lock. Consequences, in order of how badly they read:

* The script's own final branch — ``-- Key exists and is processing - lock not acquired``,
  ``return {0, false}`` — is **unreachable**. It can never be taken.
* A live lock is returned as ``{1, "processing:<token>"}``, i.e. as a *cached result*. The
  Python caller then does ``json.loads("processing:<token>")``, which raises, and logs
  "Invalid cached value" — so a perfectly ordinary contended lock is reported as data
  corruption.
* It happens to fail SAFE (that handler returns ``(None, False)`` = lock not acquired), which
  is why nothing caught fire. But the reason it is safe is a ``JSONDecodeError``, not the
  branch that was written to make it safe.

WHY THE TEST SUITE DID NOT CATCH IT, WHICH IS THE MORE IMPORTANT HALF
---------------------------------------------------------------------
``backend_app/core/cache/redis_manager.py``'s in-memory ``eval_lua`` **ignores its
``script`` argument entirely** and reimplements the decision in Python::

    if current_value and not str(current_value).startswith('processing'):
        return [1, current_value]

That reimplementation is CORRECT. So every test on this path exercised a correct Python
mock while the production Lua stayed wrong, and the Lua text was never executed by anything
in the suite. ``tests/test_atomic_idempotency_fix.py::test_lua_script_execution`` asserts
that a second call on a held key returns ``{0, False}`` — the unreachable branch — and
passes, because the mock produces that answer and the script does not. A test named for
executing Lua that executes no Lua is the gap this file closes.

WHAT IS ASSERTED, AND WHY IT IS NOT A SECOND REIMPLEMENTATION
-------------------------------------------------------------
No Lua interpreter is available here, so this file does not evaluate the script. It asserts
the one property the defect violated and which can be read straight off the text: **the
number of characters compared is not an independently-maintained magic number.** Either it
is derived from the prefix (Lua's ``#`` operator), in which case it cannot drift, or it is a
literal that must equal the length of the literal it is compared against.

That is a statement about the script, checked against the script, with the expected values
taken from the production code's own lock format — not a copy of the logic.
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core import distributed_idempotency as di

# The MODULE, and it has to be fetched this way.
#
# `backend_app/core/cache/__init__.py` re-exports the shared `SharedRedisManager` INSTANCE
# under the name `redis_manager`, which shadows the submodule of the same name on the
# package. So both `from backend_app.core.cache import redis_manager` and
# `import backend_app.core.cache.redis_manager as rm` resolve by `getattr` and hand back the
# instance, not the module — and the predicate under test lives on the `MockRedisClient`
# CLASS. `import_module` reads `sys.modules` and returns the module itself.
rm = importlib.import_module("backend_app.core.cache.redis_manager")

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What identifies a stored value as a lock, and what a new lock is written as. Read from the
#: module rather than retyped, so this file cannot be the thing that drifts.
LOCK_MARKER = getattr(di, "PROCESSING_LOCK_MARKER", "processing")
LOCK_PREFIX = getattr(di, "PROCESSING_LOCK_PREFIX", "processing:")

#: A representative in-flight lock value, built the way the code builds it.
IN_FLIGHT = f"{LOCK_PREFIX}7f3f899f-owner-token"

#: A representative finished result: valid JSON, and not prefixed.
COMPLETED = '{"status": "filled", "exchange_order_id": "X-1"}'


# ---------------------------------------------------------------------------
# Reading the script
# ---------------------------------------------------------------------------

def _lua_source() -> str:
    """The Lua script as ``_atomic_check_and_set`` defines it."""
    source = inspect.getsource(di.DistributedIdempotency._atomic_check_and_set)
    assert "redis.call" in source, (
        "the Lua script is no longer inline in _atomic_check_and_set; this file reads it "
        "from there and needs repointing at wherever it moved"
    )
    return source


#: ``string.sub(current_value, 1, <length>)`` — the length expression, whatever shape it is.
_SUB_RE = re.compile(
    r"string\.sub\(\s*current_value\s*,\s*1\s*,\s*([^)]+?)\s*\)", re.MULTILINE
)

#: The comparison that follows it, capturing the operator and the right-hand side.
_CMP_RE = re.compile(
    r"string\.sub\(\s*current_value\s*,\s*1\s*,\s*([^)]+?)\s*\)\s*(~=|==)\s*([^\s]+)",
    re.MULTILINE,
)


def _comparison() -> Tuple[str, str, str]:
    """``(length_expression, operator, right_hand_side)`` of the prefix test."""
    source = _lua_source()
    matches = _CMP_RE.findall(source)
    assert len(matches) == 1, (
        f"expected exactly one `string.sub(current_value, 1, N) <op> X` comparison in the "
        f"Lua script, found {len(matches)}: {matches!r}. More than one means the prefix "
        f"decision is made in two places and they can disagree."
    )
    length_expr, operator, rhs = matches[0]
    return length_expr.strip(), operator.strip(), rhs.strip()


# ---------------------------------------------------------------------------
# 1. The compared length is not an independent magic number
# ---------------------------------------------------------------------------

def test_the_compared_length_agrees_with_the_prefix_it_is_compared_against():
    """The defect, stated as a test.

    Before the fix: length ``7``, right-hand side ``'processing'`` (10 characters), so the
    comparison is true for every non-nil value and a live lock is misread as a result.
    """
    length_expr, operator, rhs = _comparison()

    if length_expr.startswith("#"):
        # Derived from the prefix itself. It cannot drift, so there is nothing to compare —
        # this is the preferred shape and it passes by construction.
        assert rhs.lstrip("#") == length_expr.lstrip("#") or rhs in {
            "lock_prefix",
            "prefix",
        }, (
            f"the length is derived from {length_expr!r} but the comparison is against "
            f"{rhs!r}; they must be the same value or the derivation proves nothing"
        )
        return

    assert length_expr.isdigit(), (
        f"the compared length {length_expr!r} is neither a literal integer nor derived "
        f"from the prefix with Lua's `#`. This file can only check an invariant it can "
        f"read; make the length derived and it holds by construction."
    )

    literal = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", rhs)
    assert literal, (
        f"the length is the literal {length_expr} but the comparison is against {rhs!r}, "
        f"which is not a string literal this file can measure"
    )
    compared_to = literal.group(1) if literal.group(1) is not None else literal.group(2)

    assert int(length_expr) == len(compared_to), (
        f"the Lua script compares the first {length_expr} character(s) of the stored value "
        f"against {compared_to!r}, which is {len(compared_to)} characters long. Those can "
        f"never be equal, so the comparison `{operator}` is constant: with `~=` it is TRUE "
        f"for every non-nil value, so a live lock is returned as a cached result and the "
        f"script's own `return {{0, false}}` branch is unreachable."
    )


def test_the_compared_prefix_is_the_one_the_code_actually_writes():
    """A length that agrees with the wrong prefix would still be wrong.

    The script must test for the prefix ``_atomic_check_and_set`` writes, which is read off
    the module here rather than retyped.
    """
    _length_expr, _operator, rhs = _comparison()
    literal = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", rhs)
    if not literal:
        # Derived/ARGV shape: the prefix travels as an argument, so agreement is structural.
        return
    compared_to = literal.group(1) if literal.group(1) is not None else literal.group(2)

    assert LOCK_PREFIX.startswith(compared_to) and compared_to, (
        f"the script tests for the prefix {compared_to!r}, but the lock this code writes is "
        f"{IN_FLIGHT!r}. A value written by this module must be recognised by this script."
    )


# ---------------------------------------------------------------------------
# 2. The in-flight branch is reachable
# ---------------------------------------------------------------------------

def _script_says_cached(value: str) -> bool:
    """Evaluate ONLY the parsed comparison against ``value``.

    This is not a reimplementation of the script: it applies the exact length and literal
    the script itself specifies. If those two are inconsistent, this returns what the real
    script returns, which is the whole point.
    """
    length_expr, operator, rhs = _comparison()
    literal = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", rhs)
    if length_expr.startswith("#") or not literal:
        # Derived shape: the marker travels as an ARGV and the length is `#marker`, so the
        # value under test is the marker the module defines.
        prefix = LOCK_MARKER
    else:
        prefix = literal.group(1) if literal.group(1) is not None else literal.group(2)
        n = int(length_expr)
        head = value[:n]
        return head != prefix if operator == "~=" else head == prefix

    head = value[: len(prefix)]
    return head != prefix if operator == "~=" else head == prefix


def test_a_live_lock_is_not_classified_as_a_cached_result():
    """The unreachable-branch defect, from the other side."""
    assert _script_says_cached(IN_FLIGHT) is False, (
        f"the script classifies {IN_FLIGHT!r} — a lock this module wrote — as a completed "
        f"result. The caller then json.loads() it, which raises, and logs 'Invalid cached "
        f"value' for an ordinary contended lock."
    )


def test_a_completed_result_is_still_classified_as_a_cached_result():
    """The regression half: a fix that classified everything as a lock would deadlock."""
    assert _script_says_cached(COMPLETED) is True, (
        f"the script no longer recognises {COMPLETED!r} as a completed result, so a cached "
        f"answer would never be returned and every retry would re-execute"
    )


# ---------------------------------------------------------------------------
# 3. The mock and the script must agree
# ---------------------------------------------------------------------------

#: The values whose classification the two implementations must agree on.
CLASSIFICATION_CASES = (IN_FLIGHT, COMPLETED, f"{LOCK_PREFIX}another", "processing")


@pytest.mark.parametrize("value", CLASSIFICATION_CASES)
def test_the_in_memory_mock_and_the_lua_script_agree(value: str):
    """The structural reason this defect survived, pinned.

    ``redis_manager``'s ``eval_lua`` ignores the script and reimplements the decision in
    Python. Whenever the two disagree, every test on this path measures the mock and the
    production behaviour is unobserved. This asserts they cannot disagree again.
    """
    # `MockRedisClient.eval_lua` is the one that stands in for the script in this suite.
    # `SharedRedisManager.eval_lua` delegates to whatever client is bound and raises when
    # that client cannot run Lua, so it makes no prefix decision of its own.
    assert hasattr(rm, "MockRedisClient"), (
        "the in-memory Redis client class is no longer named MockRedisClient; this "
        "assertion reads its eval_lua and needs repointing"
    )
    mock_source = inspect.getsource(rm.MockRedisClient.eval_lua)
    assert "startswith" in mock_source, (
        "the in-memory eval_lua no longer decides with startswith(); this assertion was "
        "written against that shape and needs updating with it"
    )

    # The mock's own predicate, read off its source the same way the script's is. Two
    # shapes are accepted, and one of them is strictly better:
    #
    #   startswith(PROCESSING_LOCK_MARKER)  -- references the shared definition, so
    #                                          agreement with the script is STRUCTURAL and
    #                                          there is nothing left that can drift.
    #   startswith('processing')            -- a literal, which has to be measured.
    #
    # The literal form is what shipped, and it agreeing with the script by coincidence is
    # exactly what hid the defect. Preferring the reference is the point of the fix.
    if re.search(r"startswith\(\s*PROCESSING_LOCK_MARKER\s*\)", mock_source):
        mock_prefix = LOCK_MARKER
    else:
        prefix_match = re.search(r"startswith\(\s*['\"]([^'\"]+)['\"]\s*\)", mock_source)
        assert prefix_match, (
            f"the in-memory eval_lua's prefix is neither the shared "
            f"PROCESSING_LOCK_MARKER nor a readable string literal, so its agreement with "
            f"the Lua script cannot be checked: {mock_source[:200]!r}"
        )
        mock_prefix = prefix_match.group(1)

    mock_says_cached = not value.startswith(mock_prefix)

    assert _script_says_cached(value) == mock_says_cached, (
        f"for the stored value {value!r} the Lua script says cached_result="
        f"{_script_says_cached(value)} and the in-memory mock says {mock_says_cached}. "
        f"The mock stands in for the script in every test on this path, so a disagreement "
        f"means the suite is measuring the mock and the production script is untested."
    )


# ---------------------------------------------------------------------------
# 4. No second copy of the script may drift
# ---------------------------------------------------------------------------

def test_no_copy_of_the_script_anywhere_carries_a_disagreeing_length():
    """``tests/test_atomic_idempotency_fix.py`` holds its own copy of this script.

    A copy is a second place the defect can live, and the copy is what a reader checks when
    the production one looks right. Every occurrence in the repository is held to the same
    invariant.
    """
    # Scoped to the two trees that can hold this script. A repo-wide walk here descends
    # into .venv and node_modules and takes minutes, which is how a guard gets deleted.
    searched: List[Path] = []
    for root in (REPO_ROOT / "backend_app", REPO_ROOT / "tests", REPO_ROOT / "scripts"):
        if root.is_dir():
            searched.extend(root.rglob("*.py"))
    assert searched, "the search found no Python files at all, so this guard proves nothing"

    offenders: List[str] = []
    for path in sorted(searched):
        parts = set(path.parts)
        if parts & {"__pycache__", "archive"}:
            continue
        # This file quotes the defective form in its own module docstring, deliberately, so
        # that a reader learns what went wrong without digging through history. Scanning
        # itself would make the guard permanently red for documenting the thing it guards.
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "string.sub(current_value" not in text:
            continue
        for length_expr, operator, rhs in _CMP_RE.findall(text):
            length_expr = length_expr.strip()
            if length_expr.strip().startswith("#"):
                continue
            literal = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", rhs.strip())
            if not literal or not length_expr.isdigit():
                continue
            compared_to = (
                literal.group(1) if literal.group(1) is not None else literal.group(2)
            )
            if int(length_expr) != len(compared_to):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: compares {length_expr} char(s) "
                    f"against {compared_to!r} ({len(compared_to)} chars)"
                )

    assert not offenders, (
        "a copy of the atomic check-and-set script compares a number of characters that "
        "cannot equal the prefix it is compared against:\n  " + "\n  ".join(offenders)
    )
