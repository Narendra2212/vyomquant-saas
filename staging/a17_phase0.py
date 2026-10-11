"""Phase 11B-A17. READ-ONLY. Destination safety + entry rebaseline.

RESOLVING A CONFLICT IN THE BRIEF. Phase 0 asks for `git write-tree`; the hard constraints
forbid "shared Git-object writes". `git write-tree` normally materialises tree objects into
.git/objects. Both requirements are honoured by redirecting object WRITES to a disposable
directory while still READING the repository's objects through alternates:

    GIT_OBJECT_DIRECTORY=<temp>                     # new objects land here
    GIT_ALTERNATE_OBJECT_DIRECTORIES=<repo>/.git/objects   # existing objects still readable

The tree SHA-1 is identical either way (it is a function of content), so nothing is lost and
the shared object database is never touched. The temp directory is deleted afterwards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
STAGING = os.path.join(REPO, "staging")
DEST = "PHASE11B_A17_D5_MANIFEST_RECONCILIATION.md"


def run(args: list[str], env: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        return subprocess.run(
            args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "<timeout>")


def main() -> int:
    print("=== DESTINATION SAFETY ===")
    for name in (DEST, "PHASE11B_A16_D5_CONSENT_REPORT.md"):
        p = os.path.join(STAGING, name)
        print(f"  {name:<52} exists={os.path.exists(p)}")
    dest = os.path.join(STAGING, DEST)
    print(f"  -> {'OCCUPIED, pick another name' if os.path.exists(dest) else 'FREE, safe to write'}")
    print()

    print("=== A16 / RECENT D5 REPORTS PRESENT ===")
    for n in sorted(os.listdir(STAGING)):
        if not n.startswith(("PHASE11B_A1", "PHASE11B_A9")):
            continue
        low = n.lower()
        if any(k in low for k in ("d5", "redis", "webhook", "quota", "consent")):
            st = os.stat(os.path.join(STAGING, n))
            import datetime
            ts = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
            print(f"  {st.st_size:>7} B  {ts}  {n}")
    print()

    print("=== CONCURRENT ARTIFACTS ===")
    for prefix in ("p11b14_", "p11b15_", "p11b16_", "p11b17_"):
        hits = sorted(n for n in os.listdir(STAGING) if n.startswith(prefix))
        print(f"  {prefix}*  count={len(hits)}")
        for h in hits[:40]:
            print(f"      {h}")
    print()

    print("=== ENTRY BASELINE ===")
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("origin/main", ["git", "rev-parse", "origin/main"]),
        ("divergence L/R", ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]),
    ]:
        print(f"  {label:<16} {run(args).stdout.strip()}")

    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    digest_a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    di = run(["git", "diff-index", "--cached", "HEAD"])
    staged_rows = [l for l in di.stdout.splitlines() if l.strip()]
    digest_c = hashlib.sha256("\n".join(sorted(staged_rows)).encode()).hexdigest()

    print(f"  tracked entries  {len(entries)}")
    print(f"  staged paths     {len(staged_rows)}")
    for label, args in [
        ("unstaged tracked", ["git", "diff", "--name-only"]),
        ("untracked", ["git", "ls-files", "--others", "--exclude-standard"]),
        ("unmerged", ["git", "ls-files", "-u"]),
    ]:
        print(f"  {label:<16} {len([l for l in run(args).stdout.splitlines() if l.strip()])}")
    print(f"  stash entries    {len([l for l in run(['git','stash','list']).stdout.splitlines() if l.strip()])}")
    print()

    print("=== DIGESTS (documented method, consistent with A9/A10) ===")
    print(f"  A  sha256(newline-joined raw `git ls-files -s`, file order, {len(entries)} entries)")
    print(f"       {digest_a}")
    print(f"  C  sha256(sorted `git diff-index --cached HEAD` rows, {len(staged_rows)} staged)")
    print(f"       {digest_c}")
    print()

    print("=== git write-tree (private object dir; shared ODB untouched) ===")
    tmp_obj = tempfile.mkdtemp(prefix="a17_obj_")
    env = {
        "GIT_OBJECT_DIRECTORY": tmp_obj,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.join(REPO, ".git", "objects"),
    }
    before = sum(len(f) for _, _, f in os.walk(os.path.join(REPO, ".git", "objects")))
    wt = run(["git", "write-tree"], env=env)
    after = sum(len(f) for _, _, f in os.walk(os.path.join(REPO, ".git", "objects")))
    print(f"  git write-tree exit={wt.returncode}")
    print(f"  INDEX TREE SHA-1: {wt.stdout.strip()}")
    if wt.returncode != 0:
        print(f"  stderr: {wt.stderr.strip()[:200]}")
    written = sum(len(f) for _, _, f in os.walk(tmp_obj))
    print(f"  objects written to PRIVATE dir : {written}")
    print(f"  shared .git/objects file count : {before} -> {after}  (delta {after - before})")
    shutil.rmtree(tmp_obj, ignore_errors=True)
    print(f"  private object dir deleted     : {not os.path.exists(tmp_obj)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
