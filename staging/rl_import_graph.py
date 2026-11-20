"""TASK 8 / 1A -- the rate-limit import graph, resolved statically then confirmed by import.

Question being answered: does `core/rate_limit.py` failing closed at import break any
module that the application actually loads at startup?

Method, in two independent passes so a disagreement is visible:

1. STATIC. Parse every .py under backend_app/ with `ast` and record which modules import
   `backend_app.core.rate_limit` (or the `limiter` name from it). Then walk the import
   graph forward from `backend_app/main.py` to decide which of those are reachable at
   application startup. Static, so it does not depend on an import succeeding.

2. DYNAMIC. Actually import `backend_app.main` and report success/failure. That is the
   only thing that proves the startup graph loads.

A nonce is printed first so the caller can prove this output belongs to THIS invocation
and not a stale one.
"""
from __future__ import annotations

import ast
import os
import sys
import uuid
from pathlib import Path

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BACKEND = REPO / "backend_app"
TARGET = "backend_app.core.rate_limit"


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def imports_of(path: Path) -> set[str]:
    """Every module this file imports, as dotted names (absolute only)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        print(f"UNPARSEABLE {module_name(path)}: {exc}")
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative; resolve against the package
                pkg = module_name(path).rsplit(".", node.level)[0]
                found.add(f"{pkg}.{node.module}" if node.module else pkg)
            elif node.module:
                found.add(node.module)
    return found


# ---- index every module once -------------------------------------------------
all_files = [
    p for p in sorted(BACKEND.rglob("*.py"))
    if "__pycache__" not in p.parts
]
graph: dict[str, set[str]] = {}
for path in all_files:
    graph[module_name(path)] = imports_of(path)

# ---- pass 1: who imports rate_limit -----------------------------------------
direct = sorted(m for m, imps in graph.items() if TARGET in imps)

# ---- which modules are reachable from main.py -------------------------------
reachable: set[str] = set()
frontier = ["backend_app.main"]
while frontier:
    current = frontier.pop()
    if current in reachable or current not in graph:
        continue
    reachable.add(current)
    for dep in graph[current]:
        if dep.startswith("backend_app") and dep not in reachable:
            frontier.append(dep)
        # also try package __init__ forms
        init = dep + ".__init__"
        if init in graph and init not in reachable:
            frontier.append(init)

print()
print(f"TOTAL backend_app modules parsed : {len(graph)}")
print(f"Reachable from main.py           : {len(reachable)}")
print(f"Modules importing {TARGET}: {len(direct)}")
print()
print("module | imports_rate_limit | reachable_from_main")
print("-" * 78)
for mod in direct:
    print(f"{mod} | YES | {'YES' if mod in reachable else 'no'}")

# ---- what exactly do they import -------------------------------------------
print()
print("=== imported NAMES (is anything beyond `limiter` consumed?) ===")
names_used: dict[str, set[str]] = {}
for path in all_files:
    mod = module_name(path)
    if mod not in direct:
        continue
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == TARGET:
            names_used.setdefault(mod, set()).update(
                a.name for a in node.names
            )
for mod, names in sorted(names_used.items()):
    print(f"{mod}: {sorted(names)}")

removed = {"DEV_TEST_ENVS", "env_raw"}
offenders = {m: sorted(n & removed) for m, n in names_used.items() if n & removed}
print()
print("Consumers of names removed by the fix:", offenders or "NONE")

# ---- pass 2: does the startup graph actually import -------------------------
print()
print("=== DYNAMIC: import backend_app.main ===")
print("ENV      =", os.environ.get("ENV"))
print("DEV_MODE =", os.environ.get("DEV_MODE"))
print("REDIS_URL=", repr(os.environ.get("REDIS_URL")))
try:
    import backend_app.main as _main  # noqa: F401
    print("IMPORT backend_app.main : OK")
    from backend_app.core.rate_limit import config as _cfg
    print("rate_limit config       :", _cfg)
except BaseException as exc:  # noqa: BLE001
    print(f"IMPORT backend_app.main : FAILED {type(exc).__name__}: {exc}")

print()
print(f"NONCE_END={NONCE}")
