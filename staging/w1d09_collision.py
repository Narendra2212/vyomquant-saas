"""Phase 11B-W1D-09. READ-ONLY. Collision-risk analysis from the live SQL.

Two concrete questions decide the collision policy, and neither is answerable from an
earlier report:

  Q1  Is each query scoped to ONE schema? If a query spans schemas, then `relname`,
      `conname` and `typname` are not unique on their own -- PostgreSQL only guarantees
      those names unique per namespace -- and the declared keys would collide.

  Q2  `recorded_migrations` declares key width 1 (`filename`) but orders by
      (`filename`, `step`). An ORDER BY wider than the key is the signature of multiple
      rows per key. If one file has several steps, width 1 is ambiguous BY CONSTRUCTION,
      with no database needed to see it.

Static reading only. No connection.
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import sys

os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGPORT", "1")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")
sys.path.insert(0, str(ROOT))

from scripts.verify_provisioning import SNAPSHOT_QUERIES  # noqa: E402

SCHEMA_PATTERNS = [
    (r"n\.nspname\s*=\s*'([^']+)'", "nspname = literal"),
    (r"table_schema\s*=\s*'([^']+)'", "table_schema = literal"),
    (r"schemaname\s*=\s*'([^']+)'", "schemaname = literal"),
    (r"relnamespace\s*=\s*'([^']+)'::regnamespace", "relnamespace = regnamespace"),
    (r"pronamespace\s*=\s*'([^']+)'::regnamespace", "pronamespace = regnamespace"),
    (r"typnamespace\s*=\s*'([^']+)'::regnamespace", "typnamespace = regnamespace"),
    (r"current_schema\(\)", "current_schema()"),
    (r"nspname\s+IN\s*\(([^)]*)\)", "nspname IN (...)"),
    (r"schemaname\s+IN\s*\(([^)]*)\)", "schemaname IN (...)"),
    (r"table_schema\s+IN\s*\(([^)]*)\)", "table_schema IN (...)"),
]


def main() -> int:
    print("=== Q1. SCHEMA SCOPING PER DIMENSION ===")
    print("  A key like `relname` is unique only WITHIN a namespace. If a query is not")
    print("  schema-scoped, its declared key can collide across schemas.")
    print()
    unscoped = []
    for name, sql in SNAPSHOT_QUERIES.items():
        flat = re.sub(r"\s+", " ", re.sub(r"--.*$", "", sql, flags=re.M))
        found = []
        for pat, label in SCHEMA_PATTERNS:
            for m in re.finditer(pat, flat, re.I):
                found.append(f"{label}={m.group(1) if m.groups() else 'current_schema()'}")
        status = "SCOPED" if found else "*** NOT SCHEMA-SCOPED ***"
        if not found:
            unscoped.append(name)
        print(f"  {name:<30} {status}")
        for f in dict.fromkeys(found):
            print(f"        {f}")
    print()
    print(f"  dimensions with no detectable schema predicate: {len(unscoped)}")
    for u in unscoped:
        print(f"      {u}")
    print()

    print("=== Q2. recorded_migrations: is `filename` really unique? ===")
    sql = SNAPSHOT_QUERIES["recorded_migrations"]
    print("  FULL QUERY:")
    for l in sql.strip().splitlines():
        print(f"      {l.rstrip()}")
    print()
    flat = re.sub(r"\s+", " ", sql)
    print(f"  ORDER BY wider than declared key (1)? -> ORDER BY (filename, step) = 2 terms: "
          f"{'YES' in ('YES' if 'step' in flat else 'NO')}")
    print("  Reading: an ORDER BY that adds `step` after `filename` is only necessary if")
    print("  more than one row can share a filename. If each file contributes one row per")
    print("  applied STEP, then (filename) is NOT unique and key width 1 is ambiguous.")
    print()

    print("=== Q2b. WHAT WRITES schema_migrations? (provisioner source) ===")
    prov = (ROOT / "scripts" / "provision_database.py")
    if prov.exists():
        text = prov.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, l in enumerate(lines, 1):
            if re.search(r"schema_migrations|filename|\bstep\b|PRIMARY KEY|UNIQUE", l, re.I):
                print(f"      {i:>5}: {l.strip()[:110]}")
    else:
        print("      scripts/provision_database.py not found")
    print()

    print("=== Q3. OTHER KEYS WHOSE ORDER BY IS WIDER THAN THE DECLARED KEY ===")
    print("  Same signature, same question: a wider ORDER BY hints at non-unique keys.")
    # declared widths, from the test-local map (the only declaration that exists)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "g", str(ROOT / "tests" / "test_snapshot_dimension_invariants.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    keys = dict(mod.COMPARISON_KEY)
    whole = mod.WHOLE_ROW

    def ob_terms(s: str) -> int:
        flat = re.sub(r"--.*$", "", s, flags=re.M)
        depth = in_str = 0
        i = 0
        pos = -1
        while i < len(flat):
            ch = flat[i]
            if in_str:
                if ch == "'":
                    in_str = 0
            elif ch == "'":
                in_str = 1
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and flat.lower().startswith("order by", i):
                pos = i
                break
            i += 1
        if pos < 0:
            return 0
        tail = flat[pos + 8:]
        out, depth, in_str, buf = 0, 0, 0, []
        for ch in tail:
            if in_str:
                if ch == "'":
                    in_str = 0
            elif ch == "'":
                in_str = 1
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                out += 1
        return out + 1

    for name in SNAPSHOT_QUERIES:
        k = keys.get(name)
        n = ob_terms(SNAPSHOT_QUERIES[name])
        if k == whole:
            continue
        flag = " <-- ORDER BY WIDER THAN KEY" if isinstance(k, int) and n > k else ""
        print(f"  {name:<30} key={k}  order_by_terms={n}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
