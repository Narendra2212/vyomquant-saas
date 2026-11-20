"""Phase 11B-W1D-06. READ-ONLY PROBE. Call sites, consumers, and existing test coverage.

Batched inventory so the remediation scope is decided from evidence rather than assumption:
  * who consumes SNAPSHOT_QUERIES
  * which functions carry the whole-tuple membership pattern (the defect signature)
  * which tests exercise compare() / diff_snapshots, and whether any would catch W1C-02
  * whether any test is gated on a live database (those are not evidence here)
"""

from __future__ import annotations

import os
import re

ROOT = r"c:\aerora_quant_backend_updated_final1"

SKIP_DIRS = {
    ".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
    "dist", "build", ".pytest_cache", "releases", "venv", ".venv",
}

# The defect signature: a list comprehension doing whole-row membership against the
# other side. Both known occurrences match this.
DEFECT_RE = re.compile(r"\[\s*r\s+for\s+r\s+in\s+(\w+)\s+if\s+r\s+not\s+in\s+(\w+)\s*\]")


def walk(exts=(".py",)):
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(exts):
                yield os.path.join(root, f)


def rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def main() -> int:
    print("=== 1. CONSUMERS OF SNAPSHOT_QUERIES ===")
    for p in walk():
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "SNAPSHOT_QUERIES" not in t:
            continue
        hits = [(i, l.strip()) for i, l in enumerate(t.splitlines(), 1) if "SNAPSHOT_QUERIES" in l]
        print(f"  {rel(p)}  ({len(hits)} reference(s))")
        for i, l in hits:
            print(f"      {i:>5}: {l[:104]}")
    print()

    print("=== 2. THE DEFECT SIGNATURE: whole-row membership diffing ===")
    found = []
    for p in walk():
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, l in enumerate(t.splitlines(), 1):
            if DEFECT_RE.search(l):
                found.append((rel(p), i, l.strip()))
    for f, i, l in found:
        print(f"  {f}:{i}")
        print(f"      {l[:110]}")
    print(f"  total occurrences: {len(found)}")
    print()

    print("=== 3. FUNCTIONS CONTAINING THOSE OCCURRENCES ===")
    for f, i, _l in found:
        full = os.path.join(ROOT, f.replace("/", os.sep))
        lines = open(full, encoding="utf-8", errors="replace").read().splitlines()
        fn = "<module level>"
        for j in range(i - 1, -1, -1):
            m = re.match(r"^def (\w+)", lines[j])
            if m:
                fn = m.group(1)
                break
        print(f"  {f}:{i}  ->  {fn}()")
    print()

    print("=== 4. TESTS REFERENCING THE DRIFT / PROVISIONING MODULES ===")
    for p in walk():
        if "/tests/" not in rel(p) and not rel(p).startswith("tests/"):
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not re.search(r"verify_schema_drift|verify_provisioning|diff_snapshots|\bcompare\b", t):
            continue
        n_tests = len(re.findall(r"^\s*(?:async )?def (test_\w+)", t, re.M))
        gated = bool(re.search(r"AERORA_TEST_DATABASE_URL|skipif.*DATABASE|requires_postgres", t))
        print(f"  {rel(p)}")
        print(f"      tests={n_tests}  db-gated={gated}")
        for name in ("compare(", "diff_snapshots", "COMPARISON_KEY", "modified", "MODIFIED"):
            c = t.count(name)
            if c:
                print(f"      mentions {name!r}: {c}")
    print()

    print("=== 5. DOES ANY EXISTING TEST ASSERT ON compare()'s CLASSIFICATION? ===")
    any_call = False
    for p in walk():
        r = rel(p)
        if not (r.startswith("tests/") or "/tests/" in r):
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, l in enumerate(t.splitlines(), 1):
            if re.search(r"\bcompare\s*\(", l) and "verify_schema_drift" in t:
                print(f"  {r}:{i}: {l.strip()[:100]}")
                any_call = True
    if not any_call:
        print("  NONE. No test in the repository calls verify_schema_drift.compare().")
        print("  => W1C-02 is unguarded by tests today; a regression test is genuinely new")
        print("     coverage, not a duplicate of existing assertions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
