"""SAST-01 disposition evidence.

Two questions, answered by reusing the gate's OWN detector rather than a re-implementation
(a re-implementation that agrees with the gate proves nothing about the gate):

  Q1  Are all of the gate's violations in files that are NOT in the repository?
      `tests/test_bandit_sast_gate_is_green.py` discovers modules with
      `BACKEND_APP.rglob("*.py")` -- a filesystem walk with no git awareness. CI runs on a
      fresh checkout, so an untracked working-tree file is in scope here and absent there.

  Q2  For the violations that DO exist, can the interpolated value hold anything other than
      a module-level string literal or a validator's return value?

Run:  .venv\\Scripts\\python.exe staging\\probe_sast01_scope.py
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_bandit_sast_gate_is_green import (  # noqa: E402
    REPO_ROOT,
    _constrained_names,
    _interpolated_exprs,
    _is_validator_call,
    _python_modules,
    _sql_joinedstrs,
)


def gate_violations() -> list[tuple[pathlib.Path, int, str, str]]:
    """Exactly the gate's loop, but returning structured rows instead of strings."""
    out: list[tuple[pathlib.Path, int, str, str]] = []
    for module in _python_modules():
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        constrained = _constrained_names(tree)
        for fstring, callee in _sql_joinedstrs(tree):
            for expr in _interpolated_exprs(fstring):
                if _is_validator_call(expr):
                    continue
                if isinstance(expr, ast.Name) and expr.id in constrained:
                    continue
                out.append(
                    (module, getattr(expr, "lineno", -1), callee, ast.unparse(expr))
                )
    return out


def tracked_paths() -> set[str]:
    cp = subprocess.run(
        ["git", "ls-files", "-z", "--", "backend_app"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return {p.replace("/", "\\") for p in cp.stdout.split("\0") if p}


def main() -> int:
    violations = gate_violations()
    tracked = tracked_paths()

    rows = []
    for module, lineno, callee, expr in violations:
        rel = str(module.relative_to(REPO_ROOT))
        rows.append((rel, rel in tracked, lineno, callee, expr))

    print(f"GATE_VIOLATIONS_TOTAL={len(rows)}")

    in_tracked = [r for r in rows if r[1]]
    in_untracked = [r for r in rows if not r[1]]
    print(f"IN_TRACKED_FILES={len(in_tracked)}")
    print(f"IN_UNTRACKED_FILES={len(in_untracked)}")

    print("--- BY FILE ---")
    by_file: dict[tuple[str, bool], int] = {}
    for rel, is_tracked, *_ in rows:
        by_file[(rel, is_tracked)] = by_file.get((rel, is_tracked), 0) + 1
    for (rel, is_tracked), n in sorted(by_file.items()):
        print(f"  {'TRACKED  ' if is_tracked else 'UNTRACKED'}  n={n:<3} {rel}")

    print("--- BY INTERPOLATED EXPRESSION ---")
    by_expr: dict[str, int] = {}
    for *_, expr in rows:
        by_expr[expr] = by_expr.get(expr, 0) + 1
    for expr, n in sorted(by_expr.items(), key=lambda kv: -kv[1]):
        print(f"  n={n:<3} {expr!r}")

    # Q2: why the gate demotes `column` in the bug049 module, stated as a measurement.
    target = (
        ROOT
        / "backend_app"
        / "alembic"
        / "versions"
        / "d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py"
    )
    if target.exists():
        tree = ast.parse(target.read_text(encoding="utf-8"))
        constrained = _constrained_names(tree)
        print("--- bug049 MODULE: WHY `column` IS NOT CONSTRAINED ---")
        print(f"  'table'  in constrained = {'table' in constrained}")
        print(f"  'column' in constrained = {'column' in constrained}")
        validator_assigns: list[int] = []
        other_assigns: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "column" not in names:
                    continue
                if _is_validator_call(node.value):
                    validator_assigns.append(node.lineno)
                else:
                    other_assigns.append((node.lineno, ast.unparse(node.value)))
        print(f"  `column = <validator call>` at lines: {validator_assigns}")
        print(f"  `column = <anything else>`   at lines: {other_assigns}")
        print(
            "  => the gate computes `constrained - unconstrained` MODULE-WIDE, so one "
            "rebinding of the\n     name anywhere demotes every validated use of it."
        )

    print("--- VERDICT ---")
    print(
        "SAST01_TRACKED_SCOPE_CLEAN=" + ("True" if not in_tracked else "False")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
