"""Extracts per-test failure detail from the RAW GitHub Actions job log.

The raw API log (`gh api .../actions/jobs/<id>/logs --allow-escape-sequences`) is roughly
twice the size of `gh run view --log` output and retains the pytest FAILURES section that
the latter truncated.

Each line is `<ISO timestamp> <text>`. READ-ONLY.

Usage: python staging/a8_extract_failures.py [name-substring]
"""

from __future__ import annotations

import re
import sys

LOG = "staging/a8_raw_job.log"
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")
HEADER = re.compile(r"^_{3,}\s*(.+?)\s*_{3,}$")
SHORT = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$")


def load() -> list[str]:
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        return [TS.sub("", line).rstrip() for line in handle]


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    lines = load()
    print(f"raw log lines: {len(lines)}\n")

    # 1. the short test summary, which carries one line per failure with its reason
    shorts: list[tuple[str, str, str]] = []
    for line in lines:
        m = SHORT.match(line.strip())
        if m:
            shorts.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
    print(f"=== SHORT TEST SUMMARY ENTRIES: {len(shorts)} ===")
    by_reason: dict[str, int] = {}
    for kind, node, reason in shorts:
        key = reason[:120] if reason else "(no reason on the summary line)"
        by_reason[key] = by_reason.get(key, 0) + 1
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {n:>4}  {reason}")
    print()

    # 2. FAILURES-section blocks
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        m = HEADER.match(line.strip())
        if m:
            current = m.group(1)
            blocks[current] = []
            continue
        if current is not None:
            if line.strip().startswith("=") and "warnings summary" in line:
                current = None
                continue
            blocks[current].append(line)
    print(f"=== FAILURES BLOCKS RECOVERED: {len(blocks)} ===")
    for name in list(blocks)[:40]:
        print(f"  {name}")
    print()

    if needle:
        print(f"=== DETAIL FOR BLOCKS MATCHING {needle!r} ===")
        shown = 0
        for name, body in blocks.items():
            if needle.lower() not in name.lower():
                continue
            print(f"\n--- {name} ---")
            interesting = [
                l for l in body
                if l.strip() and (
                    l.strip().startswith("E ")
                    or ".py:" in l
                    or "Error" in l
                    or "assert" in l
                    or "Failed" in l
                )
            ]
            for l in (interesting or body)[:18]:
                print(f"   {l[:210]}")
            shown += 1
            if shown >= 4:
                print("\n  ... further matching blocks truncated")
                break
        if shown == 0:
            print("  (no matching block)")
            print("  short-summary entries matching instead:")
            for kind, node, reason in shorts:
                if needle.lower() in node.lower():
                    print(f"   {kind} {node}")
                    print(f"       {reason[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
