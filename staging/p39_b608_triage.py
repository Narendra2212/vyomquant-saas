#!/usr/bin/env python3
"""
PHASE 39 / PROTOTYPE ONLY -- individual triage of the nine B608 findings.

Section 5.4 forbids assuming the nine findings share one risk profile, and
forbids suppressing them because they sit in migrations. This resolves, per
finding: what is interpolated, what is bound, what validates the interpolated
part, and whether any caller-controlled value can reach the SQL structure.

Pure source analysis. No database. No scanner. Reads only.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

FINDINGS = {
    "backend_app/alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py":
        [212, 222],
    "backend_app/alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py":
        [224, 262],
    "backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py":
        [193, 198, 209, 467, 475],
}


def head(text: str) -> None:
    print("")
    print("=" * 94)
    print(text)
    print("=" * 94)


def git_blob(rel: str) -> str:
    r = subprocess.run(["git", "hash-object", rel], cwd=str(REPO),
                       capture_output=True)
    return r.stdout.decode().strip() or "ERR"


def find_validator(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def classify_validator(node) -> tuple[str, list[str]]:
    """Does the validator RAISE on bad input, or silently sanitise it?"""
    notes = []
    raises = [n for n in ast.walk(node) if isinstance(n, ast.Raise)]
    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
    # Any .replace()/re.sub()/strip() on the way out suggests sanitising.
    sanitisers = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("replace", "sub", "strip", "translate"):
                sanitisers.append(n.func.attr)
    notes.append("raise statements: %d" % len(raises))
    notes.append("return statements: %d" % len(returns))
    notes.append("sanitising calls: %s" % (sorted(set(sanitisers)) or "none"))
    if raises and not sanitisers:
        return "FAIL-CLOSED (raises, does not sanitise)", notes
    if raises and sanitisers:
        return "MIXED (raises and sanitises)", notes
    if sanitisers:
        return "SANITISING (silently rewrites input)", notes
    return "NEITHER (no raise, no sanitise)", notes


def literal_args_only(tree: ast.Module, func_names: set[str]) -> tuple[bool, list[str]]:
    """Are the validated identifiers always literal constants at the call sites?"""
    observed: list[str] = []
    all_literal = True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = None
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname not in func_names:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                observed.append("literal %r" % arg.value)
            elif isinstance(arg, ast.Name):
                observed.append("variable %s" % arg.id)
                all_literal = False
            else:
                observed.append("expr %s" % type(arg).__name__)
                all_literal = False
    return all_literal, observed


def main() -> int:
    head("A. The nine findings, with current source hashes")
    print("")
    total = 0
    for rel, lines in FINDINGS.items():
        total += len(lines)
        print("  %s" % rel)
        print("      blob  %s" % git_blob(rel))
        print("      lines %s" % ", ".join(str(n) for n in lines))
    print("")
    print("  total findings: %d" % total)

    for rel, lines in FINDINGS.items():
        path = REPO / rel
        head("B. %s" % pathlib.Path(rel).name)
        if not path.exists():
            print("  ABSENT")
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        src_lines = src.splitlines()
        tree = ast.parse(src)

        # Which validator names does this module define?
        validators = {n.name for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and ("valid" in n.name.lower() or "check" in n.name.lower())}
        print("")
        print("  validator functions defined: %s" % (sorted(validators) or "NONE"))
        for vname in sorted(validators):
            node = find_validator(tree, vname)
            verdict, notes = classify_validator(node)
            print("    %s -> %s" % (vname, verdict))
            for note in notes:
                print("        %s" % note)

        all_lit, observed = literal_args_only(tree, validators)
        print("")
        print("  identifiers passed to those validators:")
        seen = []
        for item in observed:
            if item not in seen:
                seen.append(item)
        for item in seen[:16]:
            print("    %s" % item)
        print("")
        print("  every validated identifier is a literal constant: %s" % all_lit)

        # Is a bound parameter used alongside the interpolation?
        print("")
        print("  per-finding detail:")
        for ln in lines:
            # Gather the enclosing statement text, a few lines each way.
            lo = max(0, ln - 4)
            hi = min(len(src_lines), ln + 4)
            window = "\n".join(src_lines[lo:hi])
            interpolated = re.findall(r"\{(\w+)\}", window)
            bound = re.findall(r":(\w+)\b", window)
            bound = [b for b in bound if b not in ("re",)] + \
                    (["re"] if ":re" in window else [])
            has_param_dict = bool(re.search(r"\{\s*[\"']\w+[\"']\s*:", window))
            print("")
            print("    line %d" % ln)
            print("      f-string interpolates : %s"
                  % (sorted(set(interpolated)) or "none"))
            print("      bound parameters      : %s"
                  % (sorted(set(bound)) or "none"))
            print("      passes a params dict  : %s" % has_param_dict)
            stmt = src_lines[ln - 1].strip() if ln - 1 < len(src_lines) else ""
            print("      line text             : %s" % stmt[:88])

    head("C. Disposition")
    print("")
    print("  Each finding is an Alembic migration helper that builds SQL with the")
    print("  TABLE and COLUMN names interpolated, while every comparison against")
    print("  stored data is passed as a bound parameter (`:re`).")
    print("")
    print("  Two conditions decide the risk, and both are checked above:")
    print("    1. Does the identifier validator fail closed, or silently rewrite?")
    print("    2. Are the identifiers ever caller-supplied rather than literals?")
    print("")
    print("  SQL identifiers cannot be bound as parameters in PostgreSQL, so")
    print("  interpolation is unavoidable here; the control has to be validation.")
    print("  That is why the validator's behaviour is the whole question, and why")
    print("  'it is only a migration' is NOT an acceptable disposition.")
    print("")
    print("  No finding is suppressed, reclassified or dismissed by this probe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
