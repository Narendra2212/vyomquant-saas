"""Print the exact, numbered source of the W-6 classification ladder and its callers.

Line numbers quoted in a report have to be right, and a grep rendering is not a reliable
place to read them from. This prints them from the file itself, plus the AST-derived name and
span of the enclosing method and every call site that consumes its result.
"""

from __future__ import annotations

import ast
import pathlib
import sys

# The file contains box-drawing separators; a cp1252 stdout cannot encode them and the first
# draft of this script died on one mid-listing. Replace rather than crash: a mangled glyph in
# a section header costs nothing, a lost listing costs the evidence.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "backend_app" / "backend" / "exchange_executor.py"

text = SRC.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()
tree = ast.parse(text)


def enclosing(lineno: int) -> str:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    if best is None:
        return "<module>"
    kind = "class" if isinstance(best, ast.ClassDef) else "def"
    return f"{kind} {best.name} ({best.lineno}-{best.end_lineno})"


print("=== the classification ladder, numbered from the file ===")
anchor = next(
    i for i, ln in enumerate(lines, 1) if "Convert CCXT error to our error type" in ln
)
print(f"docstring anchor line = {anchor}  in  {enclosing(anchor)}\n")
start = anchor - 2
end = anchor + 34
for i in range(start, min(end, len(lines)) + 1):
    print(f"{i:>5}  {lines[i - 1]}")

print("\n=== the method, by AST ===")
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.lineno <= anchor <= (node.end_lineno or node.lineno):
            args = [a.arg for a in node.args.args]
            print(f"  name={node.name}  span={node.lineno}-{node.end_lineno}  args={args}")
            METHOD = node.name
            break
else:
    METHOD = None

print("\n=== every call site of that method in this file ===")
if METHOD:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name == METHOD:
                print(f"  line {node.lineno}: {lines[node.lineno - 1].strip()[:110]}")
                print(f"      in {enclosing(node.lineno)}")

print("\n=== does any caller read .retryable off the converted error? ===")
hits = [(i, ln.strip()) for i, ln in enumerate(lines, 1) if "retryable" in ln]
for i, ln in hits:
    print(f"  line {i:>5}: {ln[:110]}")
    print(f"      in {enclosing(i)}")

print("\n=== the error classes and their error_code defaults ===")
for node in tree.body:
    if isinstance(node, ast.ClassDef) and "Error" in node.name:
        bases = [getattr(b, "id", getattr(b, "attr", "?")) for b in node.bases]
        print(f"  class {node.name}({', '.join(bases)})  lines {node.lineno}-{node.end_lineno}")
