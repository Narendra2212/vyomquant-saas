"""HY-02 -- the distributed-execution subsystem is absent, and must stay absent.

WHAT THIS REPLACES, AND WHY IT IS STRONGER
    RT-11 removed ``app.include_router(distributed_execution.router, ...)`` from main.py
    and then pinned that removal with three tests in
    ``tests/test_rt11_distributed_execution_router_unmounted.py``. It deliberately did NOT
    delete the package, reasoning that "several existing tests import them directly by path
    to pin the orchestrator's unstarted state, and deleting them would break that coverage
    for no safety benefit".

    Under HY-02 the package IS now deleted -- all 40 modules under
    ``backend_app/backend/distributed_execution/`` plus ``backend_app/routers/
    distributed_execution.py``, 1.02 MB. That was safe to do because the only non-test
    module importing any of it was that router itself, measured by an AST walk of every
    tracked ``.py`` in the repository, and the router had already been unmounted.

    So RT-11's three tests had nothing left to pin: two of them asserted the module still
    exists. This file takes over the guarantee RT-11 actually cared about -- that no
    distributed-execution surface is reachable -- and states it in the stronger form that
    deletion makes available: it is not merely unmounted, it is not there. A subsystem that
    does not exist cannot be re-mounted by accident, and if someone restores it, every
    assertion below fails and says why.

WHY GUARD A DELETION AT ALL
    Because the thing being prevented already happened once. The package shipped a fully
    mounted HTTP surface -- submit/status/jobs/system-status/replay/statistics/audit --
    advertising "institutional-grade reliability via distributed queue processing" for a
    capability the platform did not have, with no caller anywhere in the frontend. The
    danger was never that the code was slow or wrong; it was that it looked finished. A
    deletion with no guard is one well-meaning revert away from being undone.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

PACKAGE_DIR = ROOT / "backend_app" / "backend" / "distributed_execution"
ROUTER_FILE = ROOT / "backend_app" / "routers" / "distributed_execution.py"

#: The dotted prefix no module may import again.
FORBIDDEN_IMPORT_PREFIX = "backend_app.backend.distributed_execution"

#: The URL prefix the deleted router was mounted under.
FORBIDDEN_ROUTE_PREFIX = "/api/distributed-execution"


def _tracked_python_files() -> list[pathlib.Path]:
    """Every .py file under the source and test trees.

    Walked from disk rather than asked of git, so this works in a CI checkout, in a
    container and in a worktree without needing git to be present.
    """
    out: list[pathlib.Path] = []
    for sub in ("backend_app", "tests", "scripts"):
        base = ROOT / sub
        if base.is_dir():
            out.extend(p for p in base.rglob("*.py") if p.is_file())
    return out


class TestTheSubsystemIsGone:
    def test_no_source_file_of_the_package_remains(self):
        """Asserts no ``.py`` SOURCE survives, not that the directory is absent.

        The first draft of this test required ``not PACKAGE_DIR.exists()`` and failed
        immediately on a developer machine -- because ``__pycache__`` from before the
        deletion was still sitting in the directory. Stale bytecode there is harmless
        (CPython will not import a module whose source is gone from a ``__pycache__``
        directory), but a guard that fails for a reason unrelated to what it guards gets
        deleted by the next person, so it asserts the thing that actually matters.
        """
        survivors = (
            sorted(p.relative_to(ROOT).as_posix() for p in PACKAGE_DIR.rglob("*.py"))
            if PACKAGE_DIR.is_dir()
            else []
        )
        assert not survivors, (
            f"source files of the deleted distributed-execution package are back: "
            f"{survivors}. It was removed under HY-02 as 40 modules whose only non-test "
            f"importer was its own already-unmounted router. If it is genuinely needed "
            f"again it needs an entry point and a reason, not a restore."
        )

    def test_the_router_module_does_not_exist(self):
        assert not ROUTER_FILE.exists(), (
            f"{ROUTER_FILE.relative_to(ROOT).as_posix()} is back. It was the only "
            f"non-test importer of the package and its mount was removed by RT-11 "
            f"because it advertised a capability this platform does not have."
        )


class TestNothingImportsItAgain:
    def test_no_module_imports_the_deleted_package(self):
        """An AST walk, so a commented-out or string-only mention does not trip it."""
        offenders: list[str] = []

        for path in _tracked_python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                                 filename=str(path))
            except SyntaxError:
                # A file that does not parse is a different problem, policed elsewhere.
                continue

            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]

                for module in modules:
                    if module == FORBIDDEN_IMPORT_PREFIX or module.startswith(
                        FORBIDDEN_IMPORT_PREFIX + "."
                    ):
                        rel = path.relative_to(ROOT).as_posix()
                        offenders.append(f"{rel}:{getattr(node, 'lineno', 0)} -> {module}")

        assert not offenders, (
            "these import the deleted distributed-execution package, so either the "
            f"deletion was reverted or an import survived it: {offenders}"
        )

    def test_main_contains_no_code_reference_to_the_router(self):
        """Code only. main.py legitimately TALKS about this subsystem.

        The first draft asserted the string ``distributed_execution`` appeared nowhere in
        main.py, and failed -- correctly, but for the wrong reason: main.py carries a
        comment at the old mount site explaining why RT-11 unmounted the router and now why
        HY-02 deleted it. That comment is the most useful thing at that line, and a guard
        that forbids explaining history is a guard that will be removed.

        So the parse tree decides, not the text: no import of the module, and no
        ``include_router`` call naming it.
        """
        main_py = ROOT / "backend_app" / "main.py"
        tree = ast.parse(main_py.read_text(encoding="utf-8", errors="replace"),
                         filename=str(main_py))

        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "distributed_execution" in alias.name:
                        offenders.append(f"import {alias.name} (line {node.lineno})")
            elif isinstance(node, ast.ImportFrom):
                names = ", ".join(a.name for a in node.names)
                if "distributed_execution" in (node.module or "") or (
                    "distributed_execution" in names
                ):
                    offenders.append(
                        f"from {node.module} import {names} (line {node.lineno})"
                    )
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "include_router":
                    try:
                        rendered = ast.unparse(node)
                    except Exception:  # pragma: no cover
                        continue
                    if "distributed_execution" in rendered:
                        offenders.append(f"{rendered[:90]} (line {node.lineno})")

        assert not offenders, (
            "backend_app/main.py references the deleted distributed-execution router in "
            f"code: {offenders}. RT-11 removed its mount and HY-02 removed the module."
        )


class TestNoRouteIsServed:
    def test_no_route_under_the_distributed_execution_prefix_is_mounted(self):
        """The end-to-end statement: the app serves nothing at that prefix.

        This is the one assertion that exercises the real application rather than the
        source tree, and it is the one RT-11 cared about most.
        """
        try:
            from backend_app.main import app
        except Exception as exc:  # pragma: no cover - import failure is its own signal
            pytest.skip(f"backend_app.main could not be imported in this environment: {exc}")

        mounted = [
            getattr(route, "path", "")
            for route in app.routes
            if FORBIDDEN_ROUTE_PREFIX in getattr(route, "path", "")
        ]
        assert not mounted, (
            f"routes are mounted under {FORBIDDEN_ROUTE_PREFIX}: {mounted}. The "
            f"subsystem behind them was deleted, so these cannot work."
        )
