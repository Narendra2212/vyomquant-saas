"""TRADE-04: is the live-trading gate ON the order path, or beside it?

WHY THIS SCRIPT EXISTS
----------------------
`staging/verify_live_trading_off.py` reported "5/5 REFUSED" and concluded that live
trading is off because `ExecutionFlags.LIVE_TRADING_ENABLED` is a hardcoded `False`. That
script invoked exactly one thing:

    SafetyMonitor.check_execution_allowed("live_order")

If that function is not called on the path that places an order, then the earlier
conclusion verified a control that sits BESIDE the order path rather than on it -- the
same "exists but is never reached" defect class this audit was commissioned to find.

So this does not ask the guard a question. It asks, by AST, WHO CALLS THE GUARD, and
whether any of those callers is on the chain

    BotRunner._inner_loop
      -> ExecutionEngine.execute_with_idempotency
        -> ExecutionEngine._execute_trade_internal
          -> CCXTExchangeExecutor.place_order
            -> ccxt create_order

Nonce-stamped at both ends so the output cannot be mistaken for a stale run.
"""
from __future__ import annotations

import ast
import sys
import uuid
from pathlib import Path

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend_app"

GATE_NAMES = {
    "check_execution_allowed",
    "assert_safe_mode",
    "validate_safety_config_integrity",
    "enforce_kill_switch",
}
FLAG_NAMES = {"ExecutionFlags", "SafetyMonitor"}

# The chain that actually reaches a venue, established by reading the code.
ORDER_PATH = [
    ("backend_app/backend/master_executor.py", "_inner_loop"),
    ("backend_app/core/execution_engine.py", "execute_with_idempotency"),
    ("backend_app/core/execution_engine.py", "_execute_trade_internal"),
    ("backend_app/backend/exchange_executor.py", "place_order"),
]


def iter_py():
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def func_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def names_in(node):
    """Every Name.id and Attribute.attr inside node, EXCLUDING its docstring."""
    body = node.body[1:] if ast.get_docstring(node) else node.body
    found = set()
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name):
                found.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                found.add(sub.attr)
    return found


# ── 1. who calls the gates, anywhere in backend_app ────────────────────────
print()
print("=== 1. CALL SITES OF THE LIVE-TRADING GATES (comments/docstrings excluded) ===")
callers: dict[str, list[tuple[str, int, str]]] = {g: [] for g in GATE_NAMES}
for path in iter_py():
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    rel = path.relative_to(REPO).as_posix()
    for fn in func_nodes(tree):
        used = names_in(fn)
        for gate in GATE_NAMES:
            if gate in used:
                callers[gate].append((rel, fn.lineno, fn.name))
    # module-level (outside any function)
    module_level = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name):
                module_level.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                module_level.add(sub.attr)
    for gate in GATE_NAMES:
        if gate in module_level:
            callers[gate].append((rel, 0, "<module level>"))

for gate in sorted(GATE_NAMES):
    sites = callers[gate]
    print(f"\n{gate}: {len(sites)} call site(s)")
    for rel, lineno, fname in sites:
        print(f"    {rel}:{lineno} in {fname}()")
    if not sites:
        print("    NONE -- this control is never invoked anywhere in backend_app/")

# ── 2. does any order-path function reference a gate? ──────────────────────
print()
print("=== 2. THE ORDER PATH, CHECKED FUNCTION BY FUNCTION ===")
print("(does this function reference a live-trading gate in EXECUTABLE code?)")
print()
any_on_path = False
for rel, fname in ORDER_PATH:
    path = REPO / rel
    if not path.exists():
        print(f"{rel}:{fname} -- FILE NOT FOUND")
        continue
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    target = [f for f in func_nodes(tree) if f.name == fname]
    if not target:
        print(f"{rel}:{fname} -- FUNCTION NOT FOUND")
        continue
    fn = target[0]
    used = names_in(fn)
    hits = sorted((GATE_NAMES | FLAG_NAMES) & used)
    verdict = ", ".join(hits) if hits else "NO GATE REFERENCE"
    if hits:
        any_on_path = True
    print(f"  {rel}:{fn.lineno} {fname}()")
    print(f"      -> {verdict}")

print()
print(f"ANY LIVE-TRADING GATE ON THE ORDER PATH: {any_on_path}")

# ── 3. what the order path DOES check ──────────────────────────────────────
print()
print("=== 3. WHAT execute_with_idempotency ACTUALLY CHECKS ===")
eng = REPO / "backend_app/core/execution_engine.py"
tree = ast.parse(eng.read_text(encoding="utf-8", errors="replace"))
fn = [f for f in func_nodes(tree) if f.name == "execute_with_idempotency"][0]
used = names_in(fn)
for probe in (
    "assert_live_environment",
    "assert_simulation_environment_matches",
    "get_global_kill_switch",
    "is_active",
    "_validate_strategy_exists",
    "check_idempotent_execution",
    "claim_execution",
    "_execute_trade_internal",
    "validate_trade",
    "validate_trade_request",
    "ExecutionGuard",
    "check_execution_allowed",
    "ExecutionFlags",
):
    print(f"    {'YES' if probe in used else 'no ':4} {probe}")

print()
print("=== 4. CONCLUSION ===")
if not any_on_path:
    print("The live-trading gate is NOT on the order path.")
    print("staging/verify_live_trading_off.py verified a control that cannot refuse an")
    print("order placed by BotRunner. Its '5/5 REFUSED' is TRUE of the function it")
    print("called and says NOTHING about whether a live order can be placed.")
else:
    print("A gate IS referenced on the order path; the earlier conclusion stands.")

print()
print(f"NONCE_END={NONCE}")
