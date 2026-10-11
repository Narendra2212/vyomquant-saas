"""Complete failure inventory from the raw GitHub Actions job log. READ-ONLY.

ENCODING IS THE WHOLE REASON THIS SCRIPT EXISTS. PowerShell's `>` redirect writes UTF-16LE
with a BOM. The A7 scripts opened the captured log as UTF-8 with `errors="replace"`, which
silently mangled every line — so A7's "60 failed / 11836 passed" came from counting
progress lines in corrupted text, and its conclusion that the FAILURES section had been
"truncated by the log transport" was wrong. The section was present and intact; the decode
was broken.

Decoded correctly, the log yields pytest's own `short test summary info`, which is one line
per failure WITH its reason — the evidence A7 reported as unobtainable.

Usage: python staging/a8_failures.py [name-substring]
"""

from __future__ import annotations

import collections
import re
import sys

LOG = "staging/a8_raw_job.log"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")
SHORT = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$")
HEADER = re.compile(r"^_{3,}\s*(.+?)\s*_{3,}$")

NEW_FILES = (
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
)


def load() -> list[str]:
    raw = open(LOG, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    return [ANSI.sub("", TS.sub("", l)).rstrip() for l in text.splitlines()]


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    lines = load()

    tally = [l for l in lines if re.search(r"\d+ failed, \d+ passed", l)]
    print("=== PYTEST FINAL TALLY ===")
    for l in tally:
        print(f"  {l.strip()[:190]}")
    print()

    # short test summary info -> one entry per failure, with reason
    start = next((i for i, l in enumerate(lines) if "short test summary" in l), None)
    entries: list[tuple[str, str, str]] = []
    if start is not None:
        for l in lines[start + 1:]:
            m = SHORT.match(l.strip())
            if m:
                entries.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
            elif l.strip().startswith("=") and entries:
                break

    print(f"=== FAILURE INVENTORY: {len(entries)} entries ===")
    per_file = collections.Counter(e[1].split("::")[0] for e in entries)
    for path, n in per_file.most_common():
        tag = "THIS COMMIT " if path in NEW_FILES else "pre-existing"
        print(f"  {n:>4}  {tag}  {path}")
    print()

    print("=== DISTINCT REASONS (all failures) ===")
    reasons = collections.Counter(e[2][:130] or "(no reason)" for e in entries)
    for reason, n in reasons.most_common(18):
        print(f"  {n:>4}  {reason}")
    print()

    if needle:
        print(f"=== ENTRIES MATCHING {needle!r} ===")
        for kind, node, reason in entries:
            if needle.lower() in node.lower():
                print(f"  {kind}  {node}")
                print(f"        {reason[:230]}")
        print()

        # full traceback blocks
        blocks: dict[str, list[str]] = {}
        current = None
        for l in lines:
            m = HEADER.match(l.strip())
            if m:
                current = m.group(1)
                blocks[current] = []
                continue
            if current is not None:
                if "warnings summary" in l or "short test summary" in l:
                    current = None
                    continue
                blocks[current].append(l)

        shown = 0
        for name, body in blocks.items():
            if needle.lower() not in name.lower():
                continue
            print(f"--- TRACEBACK: {name} ---")
            for l in [x for x in body if x.strip()][:22]:
                print(f"   {l[:200]}")
            print()
            shown += 1
            if shown >= 3:
                print("  ... further tracebacks truncated for length")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
