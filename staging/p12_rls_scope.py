"""Resolve the ENABLE/FORCE ROW LEVEL SECURITY counts, with the scope stated.

Two different numbers were produced for ENABLE in this programme -- 83 and 120 -- and a count
without a stated scope is not evidence. The repository contains several duplicate source
trees (``aerora_quant_platform/``, ``connection_layer/``, ``backend_api/``), so a recursive
count over every ``*.sql`` double-counts migrations that exist once logically.

This prints the count per top-level tree so the figure can be quoted with its scope attached,
and lists every FORCE occurrence in full so the "comment only" claim is checkable.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENABLE = re.compile(r"ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)
FORCE = re.compile(r"FORCE\s+ROW\s+LEVEL\s+SECURITY", re.I)

SKIP_DIRS = {".git", ".venv", ".codex_verify_venv", "node_modules", ".hypothesis"}


def is_comment(line: str) -> bool:
    return line.lstrip().startswith("--")


def main() -> int:
    per_tree_enable: collections.Counter[str] = collections.Counter()
    per_tree_force: collections.Counter[str] = collections.Counter()
    force_hits: list[tuple[str, int, str, bool]] = []
    enable_exec = 0
    enable_comment = 0
    files = 0

    for p in ROOT.rglob("*.sql"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        files += 1
        rel = p.relative_to(ROOT).as_posix()
        tree = rel.split("/")[0]
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            if ENABLE.search(ln):
                per_tree_enable[tree] += 1
                if is_comment(ln):
                    enable_comment += 1
                else:
                    enable_exec += 1
            if FORCE.search(ln):
                per_tree_force[tree] += 1
                force_hits.append((rel, i, ln.strip(), is_comment(ln)))

    print(f"*.sql files scanned (excluding {sorted(SKIP_DIRS)}): {files}\n")

    print("=== ENABLE ROW LEVEL SECURITY, per top-level tree ===")
    for tree, n in sorted(per_tree_enable.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {tree}/")
    print(f"  {sum(per_tree_enable.values()):>5}  TOTAL (all trees, duplicates included)")
    print(f"\n  of that total: executable={enable_exec}  in-comment={enable_comment}")

    print("\n=== FORCE ROW LEVEL SECURITY, per top-level tree ===")
    if not per_tree_force:
        print("  (none)")
    for tree, n in sorted(per_tree_force.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {tree}/")
    print(f"  {sum(per_tree_force.values()):>5}  TOTAL")

    print("\n=== every FORCE occurrence, in full ===")
    for rel, i, ln, comment in force_hits:
        print(f"  {rel}:{i}  [{'COMMENT' if comment else 'EXECUTABLE'}]")
        print(f"      {ln[:160]}")
    exec_force = [h for h in force_hits if not h[3]]
    print(f"\n  EXECUTABLE FORCE statements: {len(exec_force)}")

    print("\n=== the canonical migration trees, named explicitly ===")
    for tree in ("migrations", "backend_app/migrations"):
        d = ROOT / tree
        if not d.exists():
            print(f"  {tree}/  ABSENT")
            continue
        n_e = n_f = 0
        for p in d.rglob("*.sql"):
            t = p.read_text(encoding="utf-8", errors="replace")
            n_e += len(ENABLE.findall(t))
            n_f += len(FORCE.findall(t))
        print(f"  {tree}/  ENABLE={n_e}  FORCE={n_f}  files={len(list(d.rglob('*.sql')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
