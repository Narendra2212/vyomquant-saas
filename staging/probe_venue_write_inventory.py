"""The full create_order / cancel_order inventory, classified by RECEIVER.

Produces the table that goes into PAPER_TRADING_CERTIFICATION.md, and tells me which
receivers the isolation test does not yet classify.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, str(ROOT))

BACKEND = ROOT / "backend_app"
METHODS = {"create_order", "cancel_order"}


def receiver(node: ast.Attribute) -> str:
    value = node.value
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return "<expr>"


rows = []
for path in sorted(BACKEND.rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in METHODS:
            rows.append(
                (path.relative_to(ROOT).as_posix(), node.lineno,
                 node.func.attr, receiver(node.func))
            )

print(f"{len(rows)} create_order/cancel_order call sites in backend_app/")
print("=" * 92)
print(f"{'receiver':<26} {'method':<14} site")
print("-" * 92)
for path, line, method, recv in sorted(rows, key=lambda r: (r[3], r[0], r[1])):
    print(f"{recv:<26} {method:<14} {path}:{line}")

print()
print("BY RECEIVER")
print("=" * 92)
by_recv = {}
for path, line, method, recv in rows:
    by_recv.setdefault(recv, []).append(f"{path}:{line} ({method})")
for recv in sorted(by_recv):
    print(f"  {recv!r}: {len(by_recv[recv])}")
    for site in by_recv[recv]:
        print(f"      {site}")
