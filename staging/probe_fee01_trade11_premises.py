"""Disambiguate the two STILL-PRESENT verdicts: are the markers in executable code, or in
docstrings/comments that explain the OLD behaviour? The Phase 9 verifier used substring
counts, which cannot tell a live call from the prose documenting the fix. This re-checks
with AST (for code nodes) and comment/string stripping."""
from __future__ import annotations

import ast
import io
import pathlib
import tokenize

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def strip_comments_and_strings(src: str) -> str:
    """Return source with all comments and string-literal contents removed, so a textual
    search sees only executable tokens (operators, names, numbers)."""
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
            out.append(" ")
    except tokenize.TokenError:
        pass
    return "".join(out)


print("=" * 78)
print("FEE-01 — is UUID(int=0) in EXECUTABLE code, or only in prose about the old code?")
print("=" * 78)
fee = read("backend_app/backend/fee_engine.py")
tree = ast.parse(fee)
# AST: a UUID(int=0) call node is Call(func=Name/Attr 'UUID', keywords=[keyword(arg='int',
# value=Constant(0))]).
code_fallbacks = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    if name != "UUID":
        continue
    for kw in node.keywords:
        if kw.arg == "int" and isinstance(kw.value, ast.Constant) and kw.value.value == 0:
            code_fallbacks.append(node.lineno)
print(f"UUID(int=0) CALL NODES in executable code: {code_fallbacks}")
stripped = strip_comments_and_strings(fee)
print(f"'UUID(int=0)' in comment/string-stripped source: "
      f"{'UUID(int=0)' in stripped.replace(' ', '')}")
raises = sum(1 for n in ast.walk(tree)
             if isinstance(n, ast.Raise)
             and 'TenantResolutionError' in ast.dump(n))
print(f"TenantResolutionError raise statements: {raises}")

print()
print("=" * 78)
print("TRADE-11 — are the OLD blind-spot forms in executable code, or in prose?")
print("=" * 78)
repo = read("backend_app/core/models/execution_record.py")
engine = read("backend_app/core/execution_engine.py")

repo_stripped = strip_comments_and_strings(repo)
engine_stripped = strip_comments_and_strings(engine)

# The old SUM used NULLIF(price,'')::numeric INSIDE a SQL string literal. Stripping string
# literals removes it, so we must inspect the SQL strings specifically.
import re
sql_strings = re.findall(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', repo)
nullif_in_sql = [s[:0] for s in sql_strings if "NULLIF(price" in s]
# Find which SQL blocks still contain the OLD vanishing form vs the NEW unusable-counting form.
old_form_blocks = [i for i, s in enumerate(sql_strings)
                   if "NULLIF(price, '')::numeric" in s or "NULLIF(price,'')::numeric" in s]
print(f"SQL string blocks total: {len(sql_strings)}")
print(f"SQL blocks containing NULLIF(price...): {old_form_blocks}")
for i in old_form_blocks:
    block = sql_strings[i]
    # Show the lines mentioning price so we can judge: is this the capacity SUM, or a
    # defensive unusable-count that uses NULLIF to DETECT missing price?
    for ln in block.splitlines():
        if "price" in ln.lower() and ("nullif" in ln.lower() or "sum" in ln.lower()
                                      or "unusable" in ln.lower() or "count" in ln.lower()):
            print(f"    [{i}] {ln.strip()[:140]}")

print()
# `price or 0` in execution_engine executable code?
print(f"'abs(Decimal(str(price or 0)))' in engine comment/string-stripped source: "
      f"{'abs(Decimal(str(price or 0)))'.replace(' ', '') in engine_stripped.replace(' ', '')}")
# Show the engine lines mentioning 'price or 0'
for i, ln in enumerate(engine.splitlines(), 1):
    if "price or 0" in ln:
        # is the line a comment?
        is_comment = ln.strip().startswith("#")
        print(f"    engine L{i} (comment={is_comment}): {ln.strip()[:140]}")
