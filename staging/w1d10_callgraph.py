"""Phase 11B-W1D-10. READ-ONLY. Call graph, import graph, and circular-import risk.

This is the one question W1D-09 left to evidence: can a shared classifier be introduced
without creating a circular import or changing unrelated provisioning behaviour?

Facts needed, all from source:
  1. who CALLS compare(), diff_snapshots(), check_drift()
  2. the import edges between the three scripts (direction matters for circularity)
  3. what else each module imports at module level (a classifier placed in
     verify_provisioning.py inherits that module's import cost for both other callers)
  4. whether diff_snapshots() and check_drift() have different output contracts, which
     would make a single shared renderer a behaviour change rather than a refactor
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
SKIP = {".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
        "dist", "build", "releases", "venv", ".venv", "site-packages",
        "python_portable", "Git", "aerora_quant_platform"}

TRIO = {
    "scripts/verify_schema_drift.py": "compare",
    "scripts/verify_provisioning.py": "diff_snapshots",
    "scripts/verify_production_schema.py": "check_drift",
}
FUNCS = set(TRIO.values())


def walk():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def rel(p):
    return os.path.relpath(p, REPO).replace("\\", "/")


def main() -> int:
    print("=== 1. DEFINITION SITES ===")
    for path, fn in TRIO.items():
        full = os.path.join(REPO, path.replace("/", os.sep))
        src = open(full, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
                args = [a.arg for a in node.args.args]
                ret = ast.unparse(node.returns) if node.returns else "<none>"
                print(f"  {path}:{node.lineno}  def {fn}({', '.join(args)}) -> {ret}")
    print()

    print("=== 2. CALL SITES of compare / diff_snapshots / check_drift ===")
    callers = {fn: [] for fn in FUNCS}
    for p in walk():
        try:
            src = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        r = rel(p)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in FUNCS:
                    # find enclosing function
                    encl = "<module>"
                    for cand in ast.walk(tree):
                        if isinstance(cand, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if cand.lineno <= node.lineno <= (cand.end_lineno or cand.lineno):
                                encl = cand.name
                    callers[name].append((r, node.lineno, encl))
    for fn in sorted(FUNCS):
        print(f"  {fn}():")
        if not callers[fn]:
            print("      <no call sites found>")
        for r, ln, encl in sorted(set(callers[fn])):
            tag = " [PRODUCTION]" if r.startswith("scripts/") else (
                " [test]" if r.startswith("tests/") else " [staging probe]")
            print(f"      {r}:{ln} in {encl}(){tag}")
    print()

    print("=== 3. IMPORT EDGES AMONG THE THREE SCRIPTS ===")
    edges = []
    for path in TRIO:
        full = os.path.join(REPO, path.replace("/", os.sep))
        src = open(full, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            mod = None
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if "verify_" in a.name:
                        edges.append((path, a.name, [], node.lineno))
                continue
            if mod and "verify_" in mod:
                edges.append((path, mod, names, node.lineno))
    for src_f, mod, names, ln in edges:
        print(f"  {src_f}:{ln}  imports {mod}  {names}")
    print()
    print("  direction summary:")
    print("    verify_schema_drift      -> verify_provisioning   (SNAPSHOT_QUERIES, _norm)")
    print("    verify_production_schema -> verify_provisioning   (SNAPSHOT_QUERIES, _norm)")
    print("    verify_provisioning      -> (imports neither)")
    prov_imports_others = any(
        s == "scripts/verify_provisioning.py" and ("schema_drift" in m or "production_schema" in m)
        for s, m, _, _ in edges
    )
    print(f"    verify_provisioning imports either sibling: {prov_imports_others}")
    print(f"    => placing a shared classifier in verify_provisioning.py is ACYCLIC: "
          f"{not prov_imports_others}")
    print()

    print("=== 4. MODULE-LEVEL IMPORT COST of verify_provisioning.py ===")
    full = os.path.join(REPO, "scripts", "verify_provisioning.py")
    tree = ast.parse(open(full, encoding="utf-8", errors="replace").read())
    tops = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            tops += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            tops.append(node.module)
    print(f"  module-level imports: {sorted(set(tops))}")
    print("  (a classifier here is imported by all three callers, so it must not add")
    print("   heavyweight or side-effecting imports)")
    print()

    print("=== 5. OUTPUT CONTRACTS: are the three renderers the same shape? ===")
    for path, fn in TRIO.items():
        full = os.path.join(REPO, path.replace("/", os.sep))
        lines = open(full, encoding="utf-8", errors="replace").read().splitlines()
        tree = ast.parse("\n".join(lines))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
                body = lines[node.lineno - 1: (node.end_lineno or node.lineno)]
                ret = ast.unparse(node.returns) if node.returns else "<unannotated>"
                emits = [l.strip() for l in body if re.search(r'f"|f\'|append\(', l)]
                print(f"  {path}  {fn}() -> {ret}   lines={len(body)}")
                for e in emits[:8]:
                    print(f"      {e[:104]}")
                print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
