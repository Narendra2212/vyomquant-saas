"""Phase 11B-A8. READ-ONLY.

`backend_app/routers/exchange.py` is `MM`: it has BOTH staged and unstaged changes, and
it is one of the 22 staged paths that belong to another workstream. The guard call site
the failing test demands lives in this file. So: is that call site in the STAGED version,
the WORKTREE-only version, or both? The answer decides whether a fix for QUOTA-01 can be
committed without carrying another workstream's staged work with it.

Reads only: `git show :path` (index), `git show HEAD:path`, and the file on disk.
"""

from __future__ import annotations

import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
PATH = "backend_app/routers/exchange.py"
NEEDLES = [
    "exchange_connection_slot_guard",
    "check_exchange_connection_slot",
]


def show(spec: str) -> str | None:
    r = subprocess.run(
        ["git", "show", spec], cwd=REPO, capture_output=True, text=True, errors="replace"
    )
    return r.stdout if r.returncode == 0 else None


def report(label: str, text: str | None) -> None:
    if text is None:
        print(f"  {label:<22} <unavailable>")
        return
    lines = text.splitlines()
    print(f"  {label:<22} {len(text)} bytes, {len(lines)} lines")
    for needle in NEEDLES:
        hits = [(i, l.strip()) for i, l in enumerate(lines, 1) if needle in l]
        print(f"      {needle:<34} {len(hits)} hit(s)")
        for i, l in hits[:6]:
            print(f"          {i:>5}: {l[:120]}")


def main() -> int:
    print(f"=== {PATH} ACROSS THE THREE LOCAL VERSIONS ===")
    report("HEAD (committed)", show(f"HEAD:{PATH}"))
    print()
    report("INDEX (staged)", show(f":{PATH}"))
    print()
    disk = open(f"{REPO}\\{PATH.replace('/', chr(92))}", encoding="utf-8", errors="replace").read()
    report("WORKTREE (on disk)", disk)
    print()

    print("=== VERDICT ===")
    idx = show(f":{PATH}") or ""
    in_index = "exchange_connection_slot_guard" in idx
    in_disk = "exchange_connection_slot_guard" in disk
    print(f"  guard call site in INDEX (staged):    {in_index}")
    print(f"  guard call site in WORKTREE:          {in_disk}")
    if in_disk and not in_index:
        print("  => The wiring is WORKTREE-ONLY. Committing it means either splitting this")
        print("     file hunk-by-hunk away from the other workstream's staged edit, or")
        print("     carrying that edit along. Needs the other owner's agreement.")
    elif in_index:
        print("  => The wiring is ALREADY STAGED, mixed into another workstream's staged")
        print("     change to the same file. It cannot be committed alone without a split.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
