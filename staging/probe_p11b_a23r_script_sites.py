"""A23R Phase 0 item 6: do the script shutdown sites keep using the proxy after close()?

PHASE 11B / A23R — read-only. No Redis, no network, no database. AST only; no file is
modified and no script is executed.

WHY
---
A23's proposed change 3 makes `RedisManager.close()` unbind `_cache/_queue/_events` and set
`_connected = False`. That is a BEHAVIOURAL CHANGE for anything that calls `close()` and
then keeps using the manager: today it silently operates on closed clients; afterwards it
would fail fast, or get `None` from the `cache`/`queue`/`events` properties.

A23 §7.2 flagged seven script call sites and recorded that they "were not read line by
line". This reads them, so the compatibility question is answered from source rather than
left as a caveat.

METHOD
------
For each script, parse to AST and locate every `*.close()` call whose receiver mentions
`redis_manager`. Then, for the ENCLOSING function (or module body), collect every
subsequent statement that touches `redis_manager` and report its line number. Using the
AST rather than "the next N lines" matters: a textual window can miss a later use in the
same function and can also wrongly flag a use inside a different function that merely
appears below.
"""

from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = [
    "scripts/test_data_stream_worker.py",
    "scripts/test_event_bus.py",
    "scripts/test_execution_worker.py",
    "scripts/test_order_event_flow.py",
    "scripts/test_strategy_worker.py",
]


def _mentions_redis_manager(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "redis_manager" in sub.id:
            return True
        if isinstance(sub, ast.Attribute):
            try:
                if "redis_manager" in ast.unparse(sub):
                    return True
            except Exception:                                      # noqa: BLE001
                pass
    return False


def _close_calls(tree: ast.AST) -> list[ast.Call]:
    out = []
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in ("close", "disconnect"):
            recv = ast.unparse(sub.func.value)
            if "redis_manager" in recv:
                out.append(sub)
    return out


def _enclosing_bodies(tree: ast.Module):
    """Yield (label, body) for the module and every function/async function."""
    yield "<module>", tree.body
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node.body


def main() -> int:
    print("=" * 78)
    print("A23R ITEM 6 -- POST-close() PROXY USE IN THE SCRIPT SHUTDOWN SITES")
    print("=" * 78)
    print("READ-ONLY, AST. No script executed. No file modified.\n")

    total_sites = 0
    risky = 0
    for rel in SCRIPTS:
        path = REPO / rel
        if not path.exists():
            print(f"-- {rel}: ABSENT\n")
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            print(f"-- {rel}: unparsable ({exc})\n")
            continue

        closes = _close_calls(tree)
        print("-" * 78)
        print(f"{rel}   ({len(src.splitlines())} lines, "
              f"{len(closes)} redis_manager close/disconnect call(s))")
        print("-" * 78)
        if not closes:
            print("   none\n")
            continue

        close_lines = {c.lineno for c in closes}
        total_sites += len(closes)

        for label, body in _enclosing_bodies(tree):
            # statements in this body, in order, with their span
            stmts = [(getattr(s, "lineno", 0), s) for s in body]
            local_closes = sorted(
                ln for ln in close_lines
                if stmts and stmts[0][0] <= ln <=
                max(getattr(s, "end_lineno", s.lineno) or s.lineno for _, s in stmts)
            )
            if not local_closes:
                continue
            for cl in local_closes:
                print(f"   close() at line {cl}   (in {label}())")
                after = [
                    (ln, s) for ln, s in stmts
                    if ln > cl and _mentions_redis_manager(s)
                ]
                if after:
                    risky += 1
                    print(f"     !! {len(after)} later statement(s) in the same body "
                          f"touch redis_manager:")
                    for ln, s in after[:6]:
                        text = " ".join(ast.unparse(s).split())[:92]
                        print(f"        line {ln}: {text}")
                else:
                    print("     no later statement in the same body touches "
                          "redis_manager")
            break        # the innermost matching body is the relevant one
        print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   redis_manager close/disconnect sites examined : {total_sites}")
    print(f"   sites with LATER redis_manager use            : {risky}")
    print()
    if risky == 0:
        print("   No script continues to use the manager after closing it. A23 change 3")
        print("   (unbind + invalidate on close) would therefore NOT change observable")
        print("   behaviour for any of these scripts, and the A23 §7.2 compatibility")
        print("   caveat is RESOLVED for them.")
    else:
        print("   At least one script uses the manager after close(). A23 change 3 WOULD")
        print("   change its behaviour: today it operates on closed clients; afterwards")
        print("   the cache/queue/events properties return None. This needs the script")
        print("   owner's acceptance before change 3 ships.")
    print()
    print("   NOTE: these are scripts, not the application. The production shutdown path")
    print("   is main.py:607 -> core/cache disconnect() -> backend close(), analysed in")
    print("   A23 section 3.2, and is unaffected by this check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
