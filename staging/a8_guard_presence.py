"""Phase 11B-A8. READ-ONLY.

Does the exchange-slot guard API exist in the four revisions that matter?
  - HEAD / origin/main (the parent of two of the three D5' branches)
  - the three D5' branch commits behind PRs #8, #7, #6

Only `git grep <rev>` / `git cat-file` are used: both read object storage, neither
touches the worktree or the index.
"""

from __future__ import annotations

import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

REVS = [
    ("HEAD/main", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR#8 a624ce89", "a624ce89b16005b0c73197005d6d0672737dad65"),
    ("PR#7 49e70da2", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#6 d07014d3", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
]

PATHS = [
    "backend_app/core/subscription_dependencies.py",
    "backend_app/routers/exchange.py",
]

SYMBOLS = ["exchange_connection_slot_guard", "_slot_lock_key"]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    print("=== SYMBOL PRESENCE PER REVISION (git grep exit 0 = found, 1 = absent) ===")
    for label, rev in REVS:
        for sym in SYMBOLS:
            r = run(["git", "grep", "-n", sym, rev, "--", "*.py"])
            hits = [l for l in r.stdout.splitlines() if l.strip()]
            verdict = "FOUND" if r.returncode == 0 else "ABSENT"
            print(f"  {label:<14} {sym:<32} exit={r.returncode} {verdict} hits={len(hits)}")
        print()

    print("=== WORKTREE (tracked files, no rev = scans working tree) ===")
    for sym in SYMBOLS:
        r = run(["git", "grep", "-n", sym, "--", "*.py"])
        hits = [l for l in r.stdout.splitlines() if l.strip()]
        print(f"  worktree       {sym:<32} exit={r.returncode} hits={len(hits)}")
        for h in hits:
            print(f"                   {h[:150]}")
    print()

    print("=== FILE SIZES (bytes) ===")
    for p in PATHS:
        row = [f"{p}"]
        for label, rev in REVS:
            s = run(["git", "cat-file", "-s", f"{rev}:{p}"])
            row.append(f"{label}={s.stdout.strip() if s.returncode == 0 else 'ERR'}")
        print("  " + "  ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
