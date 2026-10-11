"""Phase 11B-A8. READ-ONLY. Index checksum changed -- establish whether STAGED CONTENT moved.

The `.git/index` SHA-256 moved from
    6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613   (entry, and A12's)
to
    3dbf1cb07e58b1e9ffb1a70154f9c008ad539188f45ab2cbe2b367fc1caf5676   (now)

The index file stores two different kinds of thing: the staged content (mode + blob SHA-1 +
path per entry) and a per-entry STAT CACHE (device, inode, mtime, ctime, size). `git status`
and `git diff` are allowed to rewrite the stat cache when it looks stale -- which it does on
this repo because `core.autocrlf` makes git re-check line endings -- and that rewrite changes
the file's bytes without changing a single staged byte.

So the checksum alone cannot distinguish "someone staged something" from "git refreshed its
own cache". This script separates the two, read-only:

  * `git ls-files -s`  -> mode, blob SHA-1, stage, path for EVERY index entry. This is the
                          staged content and nothing else. Hash it.
  * `git diff --cached --name-status` -> the 22 staged paths vs HEAD.
  * `git diff-index --cached HEAD`    -> staged blob SHA-1s, old and new.

If the entry count, the path set and every blob SHA-1 are what they were, the staged content
is intact and the checksum move was a stat-cache refresh.
"""

from __future__ import annotations

import hashlib
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    payload = "\n".join(entries).encode("utf-8")
    print("=== INDEX CONTENT (git ls-files -s) ===")
    print(f"  exit={ls.returncode}")
    print(f"  entries        : {len(entries)}")
    print(f"  SHA-256 of list: {hashlib.sha256(payload).hexdigest()}")
    print()

    st = run(["git", "diff", "--cached", "--name-status"])
    staged = [l for l in st.stdout.splitlines() if l.strip()]
    print("=== STAGED vs HEAD ===")
    print(f"  exit={st.returncode}  staged paths={len(staged)}")
    for l in staged:
        print(f"    {l}")
    print()

    di = run(["git", "diff-index", "--cached", "HEAD"])
    rows = [l for l in di.stdout.splitlines() if l.strip()]
    print("=== STAGED BLOB IDENTITIES (git diff-index --cached HEAD) ===")
    print(f"  exit={di.returncode}  rows={len(rows)}")
    for l in rows:
        print(f"    {l[:150]}")
    print()

    # Has anything been added to the index that is not in HEAD and not one of the 22?
    print("=== SANITY ===")
    unmerged = run(["git", "ls-files", "-u"])
    u = [l for l in unmerged.stdout.splitlines() if l.strip()]
    print(f"  unmerged/conflicted entries: {len(u)} (expect 0)")
    stash = run(["git", "stash", "list"])
    print(f"  stash entries: {len([l for l in stash.stdout.splitlines() if l.strip()])} (expect 0)")
    br = run(["git", "branch", "--list"])
    print(f"  local branches: {[b.strip('* ').strip() for b in br.stdout.splitlines() if b.strip()]}")
    head = run(["git", "rev-parse", "HEAD"])
    print(f"  HEAD: {head.stdout.strip()}")
    rl = run(["git", "reflog", "-n", "3", "--date=iso"])
    print("  reflog (last 3):")
    for l in rl.stdout.splitlines()[:3]:
        print(f"    {l[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
