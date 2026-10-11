"""Find EVERY test that loads a file by path at import time, and whether that path exists.

PHASE 11B / WORKSTREAM 1B — read-only.

WHY
---
pytest aborted collection on the FIRST error it hit
(`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` ->
`staging/probe_pg_find.py` missing). `Interrupted: 1 error during collection` means the
remaining files were never imported, so a second blocker of the same class would be
invisible until the first is cleared. Clearing one blocker and then discovering another is
two round trips against a 2.5-minute collection; this finds them all in one pass without
importing anything.

METHOD
------
AST, not import. For every `tests/**/*.py` the probe walks module-level statements only
(so a load inside a function body or fixture is reported separately, because it does NOT
break collection) and resolves:

  * `importlib.util.spec_from_file_location(name, PATH)` and the `_load(...)` /
    `_load_module(...)` helpers these suites use;
  * module-level `pathlib.Path`/`REPO / "x" / "y"` constants, so the probe can tell which
    concrete file a loader was pointed at;
  * bare module-level `open()` / `read_text()` / `read_bytes()` on such a constant.

Then it checks each resolved path on disk and reports MISSING ones as collection-fatal.

STRICTLY READ-ONLY: parses text, stats paths, prints. Imports nothing from the repo.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

LOADER_CALLS = {"spec_from_file_location", "_load", "_load_module", "load_module_by_path"}
READ_CALLS = {"read_text", "read_bytes", "open"}


def _const_paths(tree: ast.Module) -> dict[str, pathlib.Path]:
    """Module-level names bound to a path expression, resolved to a concrete path.

    Handles the two shapes these suites use::

        REPO = pathlib.Path(__file__).resolve().parents[1]
        BOOTSTRAP_PATH = REPO / "staging" / "bootstrap_pg.py"
    """
    env: dict[str, pathlib.Path] = {}

    def resolve(node: ast.expr) -> pathlib.Path | None:
        # REPO / "staging" / "x.py"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = resolve(node.left)
            if left is None:
                return None
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
                return left / node.right.value
            return None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        # pathlib.Path(__file__).resolve().parents[N]
        if isinstance(node, ast.Subscript):
            base = node.value
            if (isinstance(base, ast.Attribute) and base.attr == "parents"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, int)):
                return None          # caller substitutes REPO explicitly
        return None

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        src = ast.unparse(stmt.value)
        if "parents[" in src and "__file__" in src:
            env[target.id] = REPO       # by construction in every one of these files
            continue
        got = resolve(stmt.value)
        if got is not None:
            env[target.id] = got
    return env


def _calls(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _path_args(call: ast.Call, env: dict[str, pathlib.Path]) -> list[pathlib.Path]:
    out = []
    candidates = list(call.args) + [kw.value for kw in call.keywords]
    if isinstance(call.func, ast.Attribute):
        candidates.append(call.func.value)
    for arg in candidates:
        if isinstance(arg, ast.Name) and arg.id in env:
            out.append(env[arg.id])
        elif isinstance(arg, ast.BinOp):
            txt = ast.unparse(arg)
            for name, base in env.items():
                if txt.startswith(name + " /"):
                    tail = [c.value for c in ast.walk(arg)
                            if isinstance(c, ast.Constant) and isinstance(c.value, str)]
                    out.append(base.joinpath(*tail))
                    break
    return out


def main() -> int:
    fatal: list[tuple[str, int, str, pathlib.Path]] = []
    deferred: list[tuple[str, int, str, pathlib.Path]] = []
    scanned = 0
    unparsable: list[tuple[str, str]] = []

    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            unparsable.append((str(path.relative_to(REPO)), f"{exc}"))
            continue
        env = _const_paths(tree)
        if not env:
            continue
        rel = str(path.relative_to(REPO))

        # module level -> fatal to collection
        for stmt in tree.body:
            for call in _calls(stmt):
                name = _call_name(call)
                if name not in LOADER_CALLS and name not in READ_CALLS:
                    continue
                for target in _path_args(call, env):
                    if target.suffix and not target.exists():
                        fatal.append((rel, call.lineno, name, target))

        # inside functions -> a test failure, not a collection abort
        for stmt in tree.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for call in _calls(stmt):
                name = _call_name(call)
                if name not in LOADER_CALLS and name not in READ_CALLS:
                    continue
                for target in _path_args(call, env):
                    if target.suffix and not target.exists():
                        deferred.append((rel, call.lineno, name, target))

    print(f"test files scanned                      : {scanned}")
    print(f"unparsable                              : {len(unparsable)}")
    for rel, err in unparsable:
        print(f"   {rel}: {err}")
    print()
    print("=" * 78)
    print(f"COLLECTION-FATAL missing paths ({len(fatal)})")
    print("  (module-level load of a path that does not exist -> pytest aborts)")
    print("=" * 78)
    if not fatal:
        print("   none")
    for rel, line, name, target in sorted(set(fatal)):
        print(f"   {rel}:{line}  {name}()")
        print(f"       -> {target.relative_to(REPO)}   MISSING")
    print()
    print("=" * 78)
    print(f"DEFERRED missing paths ({len(set(deferred))})")
    print("  (load inside a test body -> that test FAILS, collection still succeeds)")
    print("=" * 78)
    if not deferred:
        print("   none")
    for rel, line, name, target in sorted(set(deferred)):
        print(f"   {rel}:{line}  {name}()")
        print(f"       -> {target.relative_to(REPO)}   MISSING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
