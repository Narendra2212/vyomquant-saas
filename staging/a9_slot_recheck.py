"""Phase 11B-A9. READ-ONLY. Recheck the four exchange-slot facts the brief names.

  F1  the slot test is byte-identical across the four copies after CRLF normalisation
  F2  its production API is absent from the three PR commits
  F3  the implementation is worktree-only
  F4  the exchange.py call-site hunks do NOT apply cleanly onto HEAD

F4 uses a PRIVATE index file (GIT_INDEX_FILE) seeded from HEAD, and `git apply --check`,
which validates without applying. The shared .git/index is never named, so it cannot be
touched. `subscription_dependencies.py` is run as a positive control: if the harness is
sound, that patch must APPLY while exchange.py's REFUSES.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"
PRS = {
    "PR#6": "d07014d36d32c63db44f8fb9659933c0e84e82f2",
    "PR#7": "49e70da23c2858ffc9a6f34d5db4521401477c82",
    "PR#8": "a624ce89b16005b0c73197005d6d0672737dad65",
}
SLOT = "tests/test_quota01_exchange_slot_is_atomic.py"


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e)


def blob(rev: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def main() -> int:
    print("=== F1  slot test identity across copies (CRLF-normalised) ===")
    disk = norm(open(f"{REPO}\\" + SLOT.replace("/", "\\"), "rb").read())
    print(f"  worktree  {len(disk)} bytes  sha={hashlib.sha256(disk).hexdigest()[:16]}")
    ident = True
    for lbl, sha in PRS.items():
        b = blob(sha, SLOT)
        if b is None:
            print(f"  {lbl:<8} absent")
            ident = False
            continue
        n = norm(b)
        same = n == disk
        ident &= same
        print(f"  {lbl:<8} {len(n)} bytes  sha={hashlib.sha256(n).hexdigest()[:16]}  identical={same}")
    print(f"  F1: all four identical = {ident}")
    print()

    print("=== F2/F3  guard API by revision ===")
    for sym in ("def exchange_connection_slot_guard", "def _slot_lock_key"):
        cells = []
        for lbl, sha in [("HEAD", HEAD)] + list(PRS.items()):
            b = blob(sha, "backend_app/core/subscription_dependencies.py")
            cells.append(f"{lbl}={'ok' if b and sym.encode() in b else 'ABSENT'}")
        d = open(REPO + r"\backend_app\core\subscription_dependencies.py", encoding="utf-8", errors="replace").read()
        cells.append(f"worktree={'ok' if sym in d else 'ABSENT'}")
        print(f"  {sym:<40} {'  '.join(cells)}")
    for sym in ("exchange_connection_slot_guard(",):
        cells = []
        for lbl, sha in [("HEAD", HEAD)] + list(PRS.items()):
            b = blob(sha, "backend_app/routers/exchange.py")
            cells.append(f"{lbl}={'ok' if b and sym.encode() in b else 'ABSENT'}")
        d = open(REPO + r"\backend_app\routers\exchange.py", encoding="utf-8", errors="replace").read()
        cells.append(f"worktree={'ok' if sym in d else 'ABSENT'}")
        print(f"  {'call site in exchange.py':<40} {'  '.join(cells)}")
    print()

    print("=== F4  do the worktree hunks apply onto HEAD? (private index, --check only) ===")
    tmpdir = tempfile.mkdtemp(prefix="a9_idx_")
    idx = os.path.join(tmpdir, "private_index")
    env = {"GIT_INDEX_FILE": idx}
    seed = run(["git", "read-tree", HEAD], env=env)
    print(f"  seeded private index from HEAD: exit={seed.returncode}  path={idx}")
    print(f"  shared .git/index named anywhere in this harness: False")
    print()

    for path, role in [
        ("backend_app/core/subscription_dependencies.py", "positive control (expect APPLY)"),
        ("backend_app/routers/exchange.py", "subject (expect REFUSE)"),
    ]:
        diff = run(["git", "diff", "HEAD", "--", path])
        patch = os.path.join(tmpdir, os.path.basename(path) + ".patch")
        with open(patch, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(diff.stdout)
        chk = run(["git", "apply", "--cached", "--recount", "--check", patch], env=env)
        verdict = "APPLIES" if chk.returncode == 0 else "REFUSES"
        print(f"  {path}")
        print(f"      role     : {role}")
        print(f"      patch    : {len(diff.stdout)} bytes")
        print(f"      --check  : exit={chk.returncode} -> {verdict}")
        for l in (chk.stderr or "").splitlines()[:3]:
            print(f"      stderr   : {l[:130]}")
        print()

    print("  (private index discarded; shared index untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
