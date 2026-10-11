"""Show every binding of `table`/`column` in the a1b2c3d4e5f6 migration's functions."""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
target = ROOT / "backend_app" / "alembic" / "versions" / \
    "a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py"
tree = ast.parse(target.read_text(encoding="utf-8"))

WATCH = {"table", "column"}

# module-level literal sequences
print("MODULE-LEVEL assignments of tuples/lists:")
for node in tree.body:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        tgt = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(tgt, ast.Name):
            v = node.value
            kind = type(v).__name__ if v else "None"
            print(f"  {tgt.id}: {kind} = {ast.unparse(v)[:70] if v else ''}")

for fn in ast.walk(tree):
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in WATCH:
                    print(f"{fn.name} L{node.lineno} ASSIGN {t.id} = {ast.unparse(node.value)[:70]}")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            tgts = [node.target] if isinstance(node.target, ast.Name) else \
                [e for e in getattr(node.target, "elts", []) if isinstance(e, ast.Name)]
            for t in tgts:
                if t.id in WATCH:
                    print(f"{fn.name} L{node.lineno} FOR {t.id} in {ast.unparse(node.iter)[:60]}")
