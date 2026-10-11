"""Phase 11B-W1D-09. READ-ONLY. Is `filename` a unique key for recorded_migrations?

The snapshot query is:
    SELECT filename, sha256, step FROM public.schema_migrations ORDER BY filename, step

The only key declaration that exists (test-local, W1D-05) says width 1 = (filename). But
the ORDER BY adds `step`, and `scripts/provision_database.py`'s own narrative lists the SAME
FILE being applied at two different steps. If one filename can hold two rows, width 1 is
ambiguous by construction -- a concrete key collision, provable with no database.

Checked three ways from source:
  1. the DDL for schema_migrations -- is there a PK/UNIQUE, and on what?
  2. the INSERT/record call sites -- what is written, and can a filename repeat?
  3. the step inventory -- does any file appear at more than one step?
"""

from __future__ import annotations

import io
import os
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")

SKIP = {".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
        "dist", "build", "releases", "venv", ".venv", "site-packages", "python_portable", "Git"}


def walk(exts):
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if f.endswith(exts):
                yield pathlib.Path(root) / f


def main() -> int:
    print("=== 1. DDL FOR schema_migrations ===")
    for p in walk((".sql", ".py")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"CREATE TABLE[^;]*schema_migrations[^;]*;", text, re.I | re.S):
            rel = p.relative_to(ROOT).as_posix()
            print(f"  {rel}")
            for l in m.group(0).splitlines():
                print(f"      {l.strip()[:110]}")
            print()
    print()

    print("=== 2. WRITE SITES: what is inserted into schema_migrations ===")
    for p in walk((".py", ".sql")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "schema_migrations" not in text:
            continue
        rel = p.relative_to(ROOT).as_posix()
        hits = [
            (i, l.strip()) for i, l in enumerate(text.splitlines(), 1)
            if re.search(r"INSERT\s+INTO\s+(public\.)?schema_migrations|ON CONFLICT|"
                         r"schema_migrations\s*\(", l, re.I)
        ]
        if hits:
            print(f"  {rel}")
            for i, l in hits[:12]:
                print(f"      {i:>5}: {l[:115]}")
    print()

    print("=== 3. DOES ANY FILE APPEAR AT MORE THAN ONE STEP? ===")
    prov = ROOT / "scripts" / "provision_database.py"
    if not prov.exists():
        print("  provision_database.py absent")
        return 0
    text = prov.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # The module docstring narrates the step order; find file names tied to a step label.
    step_mentions = []
    for i, l in enumerate(lines, 1):
        m = re.search(r"Step\s+(\S+)\s+(.+)", l)
        if m:
            step_mentions.append((i, m.group(1), m.group(2).strip()))
    print("  step narrative in the module docstring:")
    seen: dict[str, list[str]] = {}
    for i, step, what in step_mentions:
        print(f"      L{i:<5} step {step:<4} {what[:86]}")
        key = re.sub(r"\s+", " ", what).split("(")[0].strip()
        seen.setdefault(key, []).append(step)
    print()
    print("  artefacts mentioned at MORE THAN ONE step:")
    repeated = {k: v for k, v in seen.items() if len(set(v)) > 1}
    for k, v in repeated.items():
        print(f"      {k[:76]}  -> steps {sorted(set(v))}")
    if not repeated:
        print("      none detected in the narrative")
    print()

    print("=== 4. THE RECORDING FUNCTION ===")
    for i, l in enumerate(lines, 1):
        if re.search(r"def .*record|_record_|sha256|step\b", l) and re.match(r"^\s*(def|async def)", l):
            print(f"      {i:>5}: {l.strip()[:110]}")
    idx = next((i for i, l in enumerate(lines) if re.search(r"INSERT INTO (public\.)?schema_migrations", l, re.I)), None)
    if idx is not None:
        print("  insert context:")
        for j in range(max(0, idx - 12), min(len(lines), idx + 12)):
            print(f"      {j+1:>5}: {lines[j].rstrip()[:110]}")
    print()

    print("=== 5. CONCLUSION (static, no database) ===")
    print("  If the DDL's PK/UNIQUE is (filename, step) rather than (filename), or if any")
    print("  file is recorded at two steps, then the declared key width of 1 for")
    print("  recorded_migrations is AMBIGUOUS and would pair two distinct rows as one")
    print("  modification. Evidence above decides it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
