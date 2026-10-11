"""Phase 11B-A8. READ-ONLY. Why did .git/index change if nothing was staged?

Hypothesis: `core.autocrlf` is on, so git must re-check line endings for every file whose
stat cache looks stale. When it does, it rewrites the stat cache in .git/index -- changing
the FILE's bytes while changing no STAGED byte. `git status` and `git diff` are therefore not
byte-read-only with respect to the index, which makes the index SHA-256 a bad isolation
invariant on this repo.

Test, without staging anything:
  1. read the autocrlf / safecrlf settings
  2. hash .git/index
  3. run `git status` (a pure read command)
  4. hash .git/index again, and re-hash the STAGED CONTENT (git ls-files -s)

If the file hash moves while the staged-content hash does not, the hypothesis holds and the
earlier checksum delta is explained without any staging having occurred.
"""

from __future__ import annotations

import hashlib
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
INDEX = REPO + r"\.git\index"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def index_hash() -> tuple[str, int]:
    b = open(INDEX, "rb").read()
    return hashlib.sha256(b).hexdigest(), len(b)


def staged_hash() -> tuple[str, int]:
    r = run(["git", "ls-files", "-s"])
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines)


def main() -> int:
    print("=== CONFIG ===")
    for key in ("core.autocrlf", "core.safecrlf", "core.filemode", "core.fscache", "core.untrackedcache"):
        r = run(["git", "config", "--get", key])
        print(f"  {key:<22} = {r.stdout.strip() or '(unset)'}  (exit {r.returncode})")
    print()

    h0, n0 = index_hash()
    s0, c0 = staged_hash()
    print("=== BEFORE a pure-read git command ===")
    print(f"  .git/index      sha256={h0}  bytes={n0}")
    print(f"  staged content  sha256={s0}  entries={c0}")
    print()

    r = run(["git", "status", "--porcelain"])
    changed = len([l for l in r.stdout.splitlines() if l.strip()])
    print(f"=== ran `git status --porcelain` (exit {r.returncode}, {changed} reported paths) ===")
    print()

    h1, n1 = index_hash()
    s1, c1 = staged_hash()
    print("=== AFTER ===")
    print(f"  .git/index      sha256={h1}  bytes={n1}")
    print(f"  staged content  sha256={s1}  entries={c1}")
    print()

    print("=== VERDICT ===")
    print(f"  index file bytes moved   : {h0 != h1}")
    print(f"  staged content moved     : {s0 != s1}")
    if h0 != h1 and s0 == s1:
        print("  => CONFIRMED: a pure-read git command rewrites .git/index (stat cache) while")
        print("     leaving every staged byte alone. The index SHA-256 is not a valid")
        print("     isolation invariant here; `git ls-files -s` content hash is.")
    elif h0 == h1:
        print("  => index already settled this run; the cache was refreshed earlier.")
        print("     Staged-content hash is the invariant to quote either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
