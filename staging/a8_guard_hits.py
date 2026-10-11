"""Phase 11B-A8. READ-ONLY.

`a8_guard_presence.py` showed 19 + 6 hits for the guard symbols on all three D5' branches,
yet both production files are byte-identical to main. So the hits cannot be definitions.
This script shows WHICH FILE each hit is in, and whether any hit is a `def`.
"""

from __future__ import annotations

import collections
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

REVS = [
    ("HEAD/main", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR#8 a624ce89", "a624ce89b16005b0c73197005d6d0672737dad65"),
    ("PR#7 49e70da2", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#6 d07014d3", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    for label, rev in REVS:
        print(f"=== {label} ({rev[:8]}) ===")
        for sym in ("exchange_connection_slot_guard", "_slot_lock_key"):
            r = run(["git", "grep", "-n", sym, rev, "--", "*.py"])
            hits = [l for l in r.stdout.splitlines() if l.strip()]
            per = collections.Counter(h.split(":")[1] for h in hits if ":" in h)
            print(f"  {sym}: {len(hits)} hit(s)")
            for path, n in per.most_common():
                print(f"      {n:>3}  {path}")
            defs = [h for h in hits if "def " + sym in h or "def " + sym.lstrip("_") in h]
            print(f"      DEFINITIONS: {len(defs)}")
            for d in defs:
                print(f"        {d[:160]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
