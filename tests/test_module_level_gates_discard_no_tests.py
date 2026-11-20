"""A module-level skip gate must not sit below tests that do not need what it gates.

WHY THIS EXISTS

``pytest.skip(..., allow_module_level=True)`` and ``pytest.importorskip(...)`` abort
collection of the ENTIRE module when they fire. Placed part-way down a file, they also
discard every test defined above them -- including tests that need nothing the gate is
guarding.

Such tests then execute in NO lane, and nothing reports it as a gap:

* the gated lane does not run the file at all (``01-pr-check.yml``'s two PostgreSQL
  jobs name their files explicitly, they do not run ``tests/``), and
* the ungated lane skips the module *before* collecting those tests, so they never
  appear in its output as a skip, a failure, or anything else.

That is exactly what happened to ``tests/test_aud01_signal_audit_log.py``: a
module-level PostgreSQL gate placed after its six fake-PostgREST unit tests meant that
AUD-01's claim to prove "every writer calls it correctly and that a failure to record
the audit row never breaks the write it is recording" was tested nowhere. Collection
reported ``0 items / 1 skipped`` for a file holding nine tests. Scoping the gate to the
class that needs it turned that into ``9 items`` -- six passing, three correctly
skipped without a database.

The correct convention, already established in this suite by
``tests/test_production_security_probe_rolls_back_postgres.py``, is a per-test
``pytest.mark.skipif`` for the environment gate plus a lazy import inside the fixture
or helper that needs the driver. This guard pins that convention so the defect cannot
return silently.

WHAT IT DOES NOT CLAIM

A module-level gate at the very top of a file, before any test is defined, is correct
and common -- a file that is entirely PostgreSQL-dependent should skip wholesale. This
guard only objects when tests are defined ABOVE the gate, because only then are tests
discarded that the gate was never meant to cover.
"""

from __future__ import annotations

import ast
import pathlib
from typing import List, Optional, Tuple

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _module_gate_lineno(tree: ast.Module) -> Optional[int]:
    """Line of the earliest module-level collection-aborting gate, or ``None``.

    Recognises both forms, at module level only (a call inside a function or fixture
    is scoped to that caller and is the pattern this guard recommends):

    * ``pytest.importorskip(...)`` -- bare, or assigned to a name
    * ``pytest.skip(..., allow_module_level=True)`` -- bare, or inside an ``if``
    """
    earliest: Optional[int] = None

    def note(lineno: int) -> None:
        nonlocal earliest
        earliest = lineno if earliest is None else min(earliest, lineno)

    def calls_of(node: ast.stmt) -> List[ast.Call]:
        found: List[ast.Call] = []
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            found.append(node.value)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            found.append(node.value)
        elif isinstance(node, ast.If):
            for sub in node.body:
                if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Call):
                    found.append(sub.value)
        return found

    for node in tree.body:
        for call in calls_of(node):
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if name == "importorskip":
                note(node.lineno)
            elif name == "skip" and any(
                kw.arg == "allow_module_level" for kw in call.keywords
            ):
                note(node.lineno)
    return earliest


def _tests_defined_above(tree: ast.Module, gate_lineno: int) -> List[str]:
    """Names of tests pytest would collect that are defined above ``gate_lineno``."""
    lost: List[str] = []
    for node in tree.body:
        if node.lineno >= gate_lineno:
            continue
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for stmt in node.body:
                if isinstance(
                    stmt, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and stmt.name.startswith("test_"):
                    lost.append(f"{node.name}::{stmt.name}")
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            lost.append(node.name)
    return lost


def _offenders() -> List[Tuple[str, int, List[str]]]:
    offenders: List[Tuple[str, int, List[str]]] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            # Unparseable or unreadable files are another test's problem, not this
            # guard's: failing here would mask the thing it actually measures.
            continue
        gate = _module_gate_lineno(tree)
        if gate is None:
            continue
        lost = _tests_defined_above(tree, gate)
        if lost:
            offenders.append((path.name, gate, lost))
    return offenders


def test_no_module_level_gate_discards_tests_defined_above_it():
    offenders = _offenders()
    if offenders:
        detail = "\n".join(
            f"  {name}: module-level gate at line {gate} discards "
            f"{len(lost)} test(s) defined above it: {', '.join(lost)}"
            for name, gate, lost in offenders
        )
        raise AssertionError(
            "A module-level pytest.skip(allow_module_level=True) / "
            "pytest.importorskip aborts collection of the whole file, so these tests "
            "run in NO lane -- the gated lane does not list the file and the ungated "
            "lane skips the module before collecting them:\n"
            f"{detail}\n\n"
            "Scope the gate instead: put pytest.mark.skipif on the test(s) that need "
            "the environment, and move the driver import into the fixture or helper "
            "that uses it. See tests/test_aud01_signal_audit_log.py's _needs_pg."
        )


def test_the_scanner_detects_the_shape_it_is_meant_to_detect():
    """Negative control: the detector must flag the exact pre-fix AUD-01 shape.

    Without this, a scanner that silently stopped matching anything would make the
    guard above pass vacuously -- the same class of defect this file exists to catch.
    """
    pre_fix_shape = '''
import pytest


class TestSomethingThatNeedsNoDatabase:
    def test_a_pure_unit_assertion(self):
        assert True


psycopg2 = pytest.importorskip("psycopg2")

DSN = None

if not DSN:
    pytest.skip("needs PostgreSQL", allow_module_level=True)


class TestSomethingThatDoesNeedADatabase:
    def test_a_real_query(self, db):
        assert db
'''
    tree = ast.parse(pre_fix_shape)
    gate = _module_gate_lineno(tree)
    assert gate is not None, "the detector found no module-level gate in the pre-fix shape"

    lost = _tests_defined_above(tree, gate)
    assert lost == ["TestSomethingThatNeedsNoDatabase::test_a_pure_unit_assertion"], (
        f"expected the database-independent test to be reported as discarded, got {lost}"
    )


def test_a_gate_above_every_test_is_accepted():
    """Positive control: a wholesale-gated file is correct and must not be flagged."""
    whole_file_is_gated = '''
import pytest

pytest.importorskip("psycopg2")


class TestEverythingHereNeedsADatabase:
    def test_a_real_query(self, db):
        assert db
'''
    tree = ast.parse(whole_file_is_gated)
    gate = _module_gate_lineno(tree)
    assert gate is not None, "the detector should still see the gate"
    assert _tests_defined_above(tree, gate) == [], (
        "a gate that precedes every test discards nothing and must not be reported"
    )
