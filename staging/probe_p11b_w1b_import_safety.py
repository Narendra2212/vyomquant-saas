"""Is importing `scripts.verify_provisioning` safe, given W1B may not RUN it?

PHASE 11B / WORKSTREAM 1B — read-only, and it does NOT perform the import.

WHY
---
`scripts/verify_schema_drift.py` is W1B's read-only drift check. Line 69 of it does::

    from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm

W1B is forbidden from RUNNING `scripts/verify_provisioning.py`, and that script contains
`DROP DATABASE` / `CREATE DATABASE`. Importing a module executes every statement at its
module level. So before running the drift check, this probe answers: does importing that
file execute anything beyond definitions?

METHOD
------
AST only. The module is parsed, never imported, so a side effect cannot fire while being
measured. Every top-level statement is classified, and any statement that is not a pure
definition/import/constant is reported as a side effect. The destructive SQL is located
separately so the report can state which function bodies hold it and confirm those
functions are not called at module level.

STRICTLY READ-ONLY: parses text and prints. Imports nothing, connects to nothing.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGETS = [
    REPO / "scripts" / "verify_provisioning.py",
    REPO / "scripts" / "verify_schema_drift.py",
]

DESTRUCTIVE = ("DROP DATABASE", "CREATE DATABASE", "DROP TABLE", "DROP SCHEMA",
               "TRUNCATE", "DELETE FROM", "ALTER TABLE", "INSERT INTO", "UPDATE ")

SAFE_NODES = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
              ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr, ast.Pass)


def _is_docstring(node: ast.stmt) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def _assign_is_literal(node: ast.stmt) -> bool:
    """A module-level assignment whose value cannot call out to the database."""
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return False
    value = node.value
    if value is None:
        return True
    for sub in ast.walk(value):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            # Path arithmetic and container/str builders only.
            if name not in ("Path", "resolve", "parents", "dict", "tuple", "list",
                            "set", "frozenset", "join", "strip", "getenv", "environ",
                            "get", "format", "dedent", "sorted", "len", "str", "int"):
                return False
    return True


def _main_guarded(tree: ast.Module) -> list[str]:
    out = []
    for node in tree.body:
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "__name__" in test:
                out.append(test)
    return out


def _report(path: pathlib.Path) -> None:
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    print("=" * 78)
    print(f"{path.relative_to(REPO)}   ({len(src)} bytes, {len(tree.body)} top-level stmts)")
    print("=" * 78)

    side_effects: list[str] = []
    counts = {"docstring": 0, "import": 0, "def": 0, "class": 0,
              "literal assign": 0, "__main__ guard": 0, "other": 0}

    for node in tree.body:
        if _is_docstring(node):
            counts["docstring"] += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            counts["import"] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts["def"] += 1
        elif isinstance(node, ast.ClassDef):
            counts["class"] += 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _assign_is_literal(node):
            counts["literal assign"] += 1
        elif isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            counts["__main__ guard"] += 1
        else:
            counts["other"] += 1
            side_effects.append(f"line {node.lineno}: {ast.unparse(node)[:110]}")

    for key, val in counts.items():
        print(f"  {key:18} {val}")
    print()
    print(f"  MODULE-LEVEL SIDE EFFECTS: {len(side_effects)}")
    for item in side_effects:
        print(f"     {item}")
    if not side_effects:
        print("     none -- importing this module only binds definitions")
    print()
    print(f"  __main__ guards: {_main_guarded(tree) or 'NONE'}")

    # Where the destructive SQL lives, and whether anything calls those functions at
    # module level.
    print("  -- destructive SQL by enclosing function --")
    owners: dict[str, list[str]] = {}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                upper = sub.value.upper()
                for kw in DESTRUCTIVE:
                    if kw in upper:
                        owners.setdefault(fn.name, [])
                        if kw not in owners[fn.name]:
                            owners[fn.name].append(kw)
    if not owners:
        print("     none found")
    for fn, kws in sorted(owners.items()):
        print(f"     {fn}(): {', '.join(sorted(kws))}")

    called_at_module_level = set()
    for node in tree.body:
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if nm in owners:
                    called_at_module_level.add(nm)
    print(f"  -- destructive functions invoked at module level: "
          f"{sorted(called_at_module_level) or 'NONE'}")
    print()


def main() -> int:
    print("This probe PARSES the files. It does not import them.\n")
    for path in TARGETS:
        if path.exists():
            _report(path)
        else:
            print(f"{path.relative_to(REPO)}  MISSING\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
