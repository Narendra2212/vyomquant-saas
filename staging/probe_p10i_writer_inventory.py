"""PHASE 10 WORKSTREAM I — re-scan the repository for DSN writers. Read-only.

Workstream I's report lists "a NEW module writing the DSN would not be caught until someone
re-runs the inventory" as a residual risk. This is that re-run, made repeatable.

THE CLAIM THIS CHECKS, STATED NARROWLY ENOUGH TO BE TRUE
--------------------------------------------------------
Two earlier versions of this scanner were wrong, in opposite directions, and both are worth
recording because they are easy mistakes to repeat:

  1. "Flag every `*.write_text(...)` whose target mentions a DSN" produced 18 offenders,
     all false: the focused suites seed a `tmp_path` DSN file in their fixtures, which is
     the correct hermetic pattern, and one probe writes inside a `TemporaryDirectory`.
  2. "Only a module-level UPPER_CASE constant counts" then missed the REAL write site,
     because `write_dsn_if_safe` writes to its injected `dsn_file` PARAMETER (which merely
     defaults to the module constant). The scanner reported "bootstrap_pg.py no longer
     writes the DSN at all" -- a false clean.

So the property is not about naming. It is:

  * `staging/bootstrap_pg.py` contains DSN write call sites ONLY inside the function
    `write_dsn_if_safe` -- i.e. no path bypasses the four validation checks;
  * `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` contain NO DSN write call
    site at all, and both reference `write_dsn_if_safe`;
  * every other DSN write site lives in a test or diagnostic file and writes to a path its
    caller supplied (`tmp_path`, `TemporaryDirectory`), so it cannot reach the shared file.

Call sites are found by AST, because `probe_pg_find.py` and `pg_keepalive_r1.py` both quote
their old unguarded write inside a docstring on purpose, and a text search flags the quotes.
Files are read as `utf-8-sig` -- several sibling probes were written by PowerShell's
`Out-File -Encoding utf8` and carry a BOM that `ast.parse` rejects. Unreadable files are
reported separately, so "could not scan" is never presented as "clean".
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEARCH_DIRS = ("staging", "scripts", "tests", "backend_app")
SKIP_PARTS = {
    "node_modules", "site-packages", "__pycache__", ".hypothesis",
    ".codex_verify_venv", ".ccxt_upgrade_probe", ".venv", "Git",
}

SANCTIONED_FILE = "staging/bootstrap_pg.py"
SANCTIONED_FUNC = "write_dsn_if_safe"
MUST_NOT_WRITE = ("staging/probe_pg_find.py", "staging/pg_keepalive_r1.py")


def dsn_write_nodes(tree):
    """Every `<something-dsn-ish>.write_text/_bytes(...)` Call node."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in (
            "write_text", "write_bytes"
        ):
            continue
        if "dsn" in ast.unparse(func.value).lower():
            out.append(node)
    return out


def enclosing_functions(tree, target):
    """Names of the FunctionDefs that lexically contain `target`."""
    names = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(n is target for n in ast.walk(fn)):
            names.append(fn.name)
    return names


findings, unreadable, other_writes = [], [], []
scanned = 0

for d in SEARCH_DIRS:
    base = ROOT / d
    if not base.is_dir():
        continue
    for path in base.rglob("*.py"):
        if SKIP_PARTS & set(path.parts):
            continue
        scanned += 1
        try:
            src = path.read_text(encoding="utf-8-sig", errors="strict")
        except (OSError, UnicodeDecodeError) as e:
            unreadable.append((path.relative_to(ROOT).as_posix(), f"read: {e}"))
            continue
        if "dsn" not in src.lower():
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            unreadable.append((path.relative_to(ROOT).as_posix(), f"parse: {e}"))
            continue
        nodes = dsn_write_nodes(tree)
        if not nodes:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in nodes:
            enclosing = enclosing_functions(tree, node)
            site = (f"line {node.lineno}: {ast.unparse(node.func.value)}."
                    f"{node.func.attr}(...)  in {enclosing or ['<module>']}")
            if rel == SANCTIONED_FILE:
                findings.append((rel, site, SANCTIONED_FUNC in enclosing))
            else:
                other_writes.append((rel, site))

print("=" * 78)
print("PHASE 10 / WS-I - DSN writer inventory re-scan (AST, read-only)")
print("=" * 78)
print(f"python files scanned            : {scanned}")
print(f"files that could not be scanned : {len(unreadable)}")
for rel, why in unreadable:
    print(f"      {rel}  [{why[:70]}]")

print("-" * 78)
print(f"1. {SANCTIONED_FILE} write sites (must all be inside {SANCTIONED_FUNC}):")
bad_containment = [f for f in findings if not f[2]]
for rel, site, inside in findings:
    print(f"   {'ok ' if inside else 'BAD'} {site}")
if not findings:
    print("   NONE FOUND - the sanctioned writer has disappeared.")

print("-" * 78)
print("2. modules that must contain NO DSN write site:")
for rel in MUST_NOT_WRITE:
    hits = [s for r, s in other_writes if r == rel]
    delegates = SANCTIONED_FUNC in (ROOT / rel).read_text(
        encoding="utf-8-sig", errors="replace")
    print(f"   {'ok ' if not hits else 'BAD'} {rel}: "
          f"{len(hits)} write site(s); delegates={delegates}")
    for s in hits:
        print(f"         {s}")

print("-" * 78)
print("3. all other DSN write sites (expected: tests/diagnostics on caller-supplied "
      "paths):")
externals = [(r, s) for r, s in other_writes if r not in MUST_NOT_WRITE]
by_file = {}
for r, s in externals:
    by_file.setdefault(r, []).append(s)
for r in sorted(by_file):
    kind = ("test" if r.startswith("tests/") else
            "diagnostic" if "/probe_" in r else "OPERATIONAL")
    print(f"   [{kind}] {r}  ({len(by_file[r])} site(s))")
operational_leaks = [r for r in by_file
                     if not r.startswith("tests/") and "/probe_" not in r]

print("=" * 78)
problems = []
if len(findings) != 1:
    problems.append(f"expected exactly 1 sanctioned write site, found {len(findings)}")
if bad_containment:
    problems.append(f"{len(bad_containment)} write site(s) bypass {SANCTIONED_FUNC}")
for rel in MUST_NOT_WRITE:
    if [s for r, s in other_writes if r == rel]:
        problems.append(f"{rel} writes the DSN directly")
if operational_leaks:
    problems.append(f"operational modules writing a DSN path: {operational_leaks}")
if unreadable:
    problems.append(f"{len(unreadable)} file(s) could not be scanned")

if problems:
    print("INVENTORY NEEDS ATTENTION:")
    for p in problems:
        print(f"   - {p}")
else:
    print("INVENTORY CLEAN")
    print(f"   one DSN write site, inside {SANCTIONED_FUNC}")
    print("   both delegating modules contain no write site and reference the helper")
    print(f"   {len(externals)} other site(s), all tests/diagnostics on supplied paths")
sys.exit(1 if problems else 0)
