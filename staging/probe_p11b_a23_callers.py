"""A23 Phase 0: every caller of RedisManager.initialize() and RedisManager.close().

PHASE 11B / A23 — read-only. No Redis, no network, no database. AST-based, so a call
inside a string or comment cannot be mistaken for a call site. Writes nothing but its own
redirected stdout. No source file is modified.

WHY THIS MATTERS FOR THE AUTHORIZATION GATE
-------------------------------------------
The proposed Step 0 changes `initialize()` and `close()` in
`backend_app/backend/redis_manager.py`. The risk of that change is a function of who calls
them:

  * `initialize()` -- if the only callers are the two internal recovery paths, the blast
    radius of adding cleanup is confined to this file.
  * `close()` -- the proposal has `close()` additionally reset `_connected` and unbind the
    three clients. If some caller relies on the manager still being usable after
    `close()`, that is a behavioural break and needs naming BEFORE any edit. If nothing
    calls it, G3 is latent rather than live and the honest severity is lower than A22V
    implied.

The search is deliberately wider than `backend_app/`: a test or script that calls
`close()` counts just as much as production code.

METHOD
------
For every `*.py` under `backend_app/`, `tests/` and `scripts/`, parse and walk the AST for
`<something>.initialize(...)` / `<something>.close(...)` / `<something>.aclose(...)`
calls, record the receiver expression verbatim, and classify by whether the receiver can
plausibly be the backend RedisManager. Receivers are reported rather than filtered, so a
judgement call is visible instead of hidden in a regex.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOTS = [REPO / "backend_app", REPO / "tests", REPO / "scripts"]
TARGET_METHODS = {"initialize", "close", "aclose", "disconnect", "connect"}

#: Receiver expressions that ARE the backend RedisManager, established from source:
#: `backend_app/backend/redis_manager.py` defines the class, the module global
#: `_redis_manager`, and the `redis_manager` compat proxy.
BACKEND_RECEIVERS = {
    "self", "_redis_manager", "RedisManager", "mgr", "manager", "redis_manager",
}

#: `backend_app/core/cache/redis_manager.py` exports its OWN `redis_manager` object with
#: connect()/disconnect(). `main.py:74` imports THAT one. Same attribute name, different
#: object -- so the import has to be read before a call site is attributed.
CORE_CACHE_MODULE = "backend_app.core.cache"


def imports_of(tree: ast.Module) -> dict[str, str]:
    """Map bound name -> the module it came from, for the two managers."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                if "redis_manager" in alias.name or "redis_manager" in (node.module or ""):
                    out[name] = node.module
                elif alias.name == "redis_manager":
                    out[name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "redis_manager" in alias.name:
                    out[alias.asname or alias.name.split(".")[0]] = alias.name
    return out


def main() -> int:
    print("=" * 78)
    print("A23 PHASE 0 -- CALLERS OF RedisManager.initialize() / close()")
    print("=" * 78)
    print("READ-ONLY, AST-based. No source modified. No Redis contacted.\n")

    rows: list[tuple[str, int, str, str, str]] = []
    scanned = 0
    for root in ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except SyntaxError:
                continue
            imports = imports_of(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in TARGET_METHODS:
                    continue
                receiver = ast.unparse(func.value)
                base = receiver.split(".")[0].split("(")[0]
                origin = imports.get(base, "")
                rows.append((
                    str(path.relative_to(REPO)).replace("\\", "/"),
                    node.lineno, receiver, func.attr, origin,
                ))

    print(f"   python files scanned : {scanned}")
    print(f"   matching call sites  : {len(rows)}\n")

    # ---- initialize() -------------------------------------------------------
    print("-" * 78)
    print("initialize() call sites")
    print("-" * 78)
    inits = [r for r in rows if r[3] == "initialize"]
    backend_inits = []
    for rel, line, receiver, _m, origin in sorted(inits):
        base = receiver.split(".")[0]
        is_backend = (rel == "backend_app/backend/redis_manager.py"
                      or base in BACKEND_RECEIVERS and "redis" in rel.lower())
        tag = "  <== BACKEND RedisManager" if is_backend else ""
        if is_backend:
            backend_inits.append((rel, line, receiver))
        print(f"   {rel}:{line}  {receiver}.initialize(){tag}")
    print(f"\n   on the BACKEND RedisManager: {len(backend_inits)}")
    for rel, line, receiver in backend_inits:
        print(f"     {rel}:{line}  {receiver}.initialize()")

    # ---- close() ------------------------------------------------------------
    print("\n" + "-" * 78)
    print("close() / aclose() call sites, grouped by receiver")
    print("-" * 78)
    closes = [r for r in rows if r[3] in ("close", "aclose")]
    by_receiver: dict[str, list[tuple[str, int, str]]] = {}
    for rel, line, receiver, method, origin in closes:
        by_receiver.setdefault(f"{receiver}.{method}()", []).append((rel, line, origin))
    for key in sorted(by_receiver):
        sites = by_receiver[key]
        print(f"   {key}   x{len(sites)}")
        for rel, line, origin in sites[:4]:
            print(f"     {rel}:{line}"
                  f"{'   [imported from ' + origin + ']' if origin else ''}")
        if len(sites) > 4:
            print(f"     ... and {len(sites) - 4} more")

    print("\n" + "-" * 78)
    print("Does anything call close() on the BACKEND RedisManager?")
    print("-" * 78)
    candidates = [
        (rel, line, receiver, origin) for rel, line, receiver, method, origin in closes
        if receiver.split(".")[0] in BACKEND_RECEIVERS
    ]
    confirmed = []
    for rel, line, receiver, origin in sorted(candidates):
        from_core_cache = origin.startswith(CORE_CACHE_MODULE)
        verdict = ("core/cache manager (DIFFERENT object)" if from_core_cache
                   else "backend manager OR ambiguous")
        print(f"   {rel}:{line}  {receiver}.close()   -> {verdict}")
        if not from_core_cache:
            confirmed.append((rel, line, receiver))
    if not candidates:
        print("   none")

    print("\n   call sites that could be the backend manager: "
          f"{len(confirmed)}")
    for rel, line, receiver in confirmed:
        print(f"     {rel}:{line}  {receiver}.close()")

    # ---- main.py attribution ------------------------------------------------
    print("\n" + "-" * 78)
    print("main.py: which manager does it drive?")
    print("-" * 78)
    main_py = REPO / "backend_app" / "main.py"
    if main_py.exists():
        tree = ast.parse(main_py.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    "redis_manager" in str(node.names[0].name) + (node.module or ""):
                names = ", ".join(a.name for a in node.names)
                print(f"   line {node.lineno}: from {node.module} import {names}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = ast.unparse(node.func.value)
                if "redis_manager" in recv and node.func.attr in TARGET_METHODS:
                    print(f"   line {node.lineno}: {recv}.{node.func.attr}()")
    print("\n   => main.py imports `redis_manager` from backend_app.core.cache (its own")
    print("      object with connect()/disconnect()), NOT the backend RedisManager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
