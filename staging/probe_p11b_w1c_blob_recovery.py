"""Exhaustive search of EVERY git object for the two missing staging sources.

PHASE 11B / WORKSTREAM 1C — Phase 0 recovery evidence. READ-ONLY with respect to git:
it only reads objects via `git cat-file`; it creates no ref, commit, stash or index entry.

WHY
---
W1B concluded `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` were never
committed, on the strength of `git log --all` and a `git rev-list --all | ls-tree` sweep.
Both of those only see REACHABLE history. W1C's Phase 0 found two things W1B missed:

  * `git stash list` is NOT empty -- it has two entries, one of them a
    `filter-branch: rewrite` artifact. A filter-branch rewrote this repository's history,
    which means the pre-rewrite objects may still exist while being unreachable.
  * `git fsck --lost-found` reports 112 dangling commits and 30 dangling non-commit
    objects.

An unreachable blob is still a perfect copy of the file. If either module is in there,
recovery is EXACT and no reconstruction is needed. That is worth an exhaustive check
before anything is authored by hand.

METHOD
------
Three passes, widest last:

  1. the 30 `.git/lost-found/other/*` objects, by content;
  2. every tree of every dangling commit, by path name;
  3. EVERY object in the repository (`git cat-file --batch-all-objects`), matching blobs
     on distinctive identifiers recovered from the surviving bytecode.

The match strings come from the `.pyc` recovery, not from guesses: `find_and_record`,
`candidate_dsns`, `_port_from_pid_file`, `HISTORICAL_PORTS`, `PG_STARTED port=`,
`KEEPALIVE: sleeping`, `PG_DSN_NOT_RECORDED`.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Identifiers recovered from the surviving bytecode. A blob containing several of these
#: IS one of the missing modules; a blob containing one might merely mention it.
FINGERPRINTS = {
    "probe_pg_find": [
        "def find_and_record", "def candidate_dsns", "def _port_from_pid_file",
        "HISTORICAL_PORTS", "DEFAULT_INTENDED_DIR", "_probe_find_bootstrap_pg",
        "NO SUITABLE CANDIDATE", "--any-provisioning",
    ],
    "pg_keepalive_r1": [
        "KEEPALIVE: sleeping", "PG_DSN_NOT_RECORDED", "PG_STARTED port=",
        "PG_NEVER_READY", "_keepalive_bootstrap_pg", "PG_READY ",
    ],
}

TARGET_PATHS = ("staging/probe_pg_find.py", "staging/pg_keepalive_r1.py")


def _git(*args: str, binary: bool = False):
    res = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                         timeout=600)
    if binary:
        return res.returncode, res.stdout
    return res.returncode, res.stdout.decode("utf-8", errors="replace")


def _score(text: str) -> dict[str, int]:
    return {name: sum(1 for token in tokens if token in text)
            for name, tokens in FINGERPRINTS.items()}


def pass1_lost_found() -> list[str]:
    print("=" * 78)
    print("PASS 1 -- the 30 `.git/lost-found/other` objects, by content")
    print("=" * 78)
    found = []
    root = REPO / ".git" / "lost-found" / "other"
    if not root.exists():
        print("   .git/lost-found/other absent (run `git fsck --lost-found` first)")
        return found
    objs = sorted(p.name for p in root.iterdir() if p.is_file())
    print(f"   objects to examine: {len(objs)}")
    for oid in objs:
        rc, kind = _git("cat-file", "-t", oid)
        kind = kind.strip()
        rc, size = _git("cat-file", "-s", oid)
        if kind != "blob":
            print(f"   {oid[:12]} {kind:6} {size.strip():>8}  (not a blob)")
            continue
        rc, data = _git("cat-file", "blob", oid, binary=True)
        text = data.decode("utf-8", errors="replace")
        scores = _score(text)
        best = max(scores, key=lambda k: scores[k])
        flag = ""
        if scores[best] >= 3:
            flag = f"  <<< MATCH {best} ({scores[best]} fingerprints)"
            found.append(oid)
        first = text.strip().splitlines()[0][:52] if text.strip() else "(empty)"
        print(f"   {oid[:12]} blob   {size.strip():>8}  {first!r}{flag}")
    return found


def pass2_dangling_commits() -> list[str]:
    print("\n" + "=" * 78)
    print("PASS 2 -- every tree of every dangling commit, by path name")
    print("=" * 78)
    root = REPO / ".git" / "lost-found" / "commit"
    hits = []
    if not root.exists():
        print("   .git/lost-found/commit absent")
        return hits
    commits = sorted(p.name for p in root.iterdir() if p.is_file())
    print(f"   dangling commits to walk: {len(commits)}")
    for oid in commits:
        rc, names = _git("ls-tree", "-r", "--name-only", oid)
        if rc != 0:
            continue
        for path in TARGET_PATHS:
            if path in names:
                hits.append(f"{oid} :: {path}")
                print(f"   {oid[:12]}  CONTAINS {path}")
    print(f"   hits: {len(hits) or 'none'}")
    return hits


def pass3_all_objects() -> list[str]:
    print("\n" + "=" * 78)
    print("PASS 3 -- EVERY object in the repository, blobs matched by fingerprint")
    print("=" * 78)
    rc, listing = _git("cat-file", "--batch-all-objects", "--batch-check")
    if rc != 0:
        print("   could not enumerate objects")
        return []
    blobs = []
    total = 0
    for line in listing.splitlines():
        parts = line.split()
        total += 1
        if len(parts) == 3 and parts[1] == "blob":
            size = int(parts[2])
            # The sources were 10,716 and 4,198 bytes. Allow a wide band for any
            # earlier or later revision, but skip objects that cannot be either.
            if 1_000 <= size <= 60_000:
                blobs.append((parts[0], size))
    print(f"   objects in repository : {total}")
    print(f"   candidate blobs       : {len(blobs)} (1KB..60KB)")

    hits = []
    for oid, size in blobs:
        rc, data = _git("cat-file", "blob", oid, binary=True)
        if rc != 0:
            continue
        text = data.decode("utf-8", errors="replace")
        scores = _score(text)
        for name, score in scores.items():
            if score >= 3:
                hits.append((oid, size, name, score))
                print(f"   {oid[:12]} {size:>7}B  MATCH {name} "
                      f"({score}/{len(FINGERPRINTS[name])} fingerprints)")
    print(f"   matching blobs: {len(hits) or 'NONE'}")
    return hits


def main() -> int:
    print(f"repo: {REPO}\n")
    for target in TARGET_PATHS:
        print(f"   {target:34} on disk = {(REPO / target).exists()}")
    print()
    a = pass1_lost_found()
    b = pass2_dangling_commits()
    c = pass3_all_objects()
    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"   pass 1 (lost-found blobs)   : {len(a)} match(es)")
    print(f"   pass 2 (dangling commits)   : {len(b)} match(es)")
    print(f"   pass 3 (all objects)        : {len(c)} match(es)")
    if not (a or b or c):
        print("\n   EXACT SOURCE RECOVERY FROM GIT IS NOT POSSIBLE.")
        print("   Neither module exists as any object in this repository -- reachable,")
        print("   unreachable, dangling, stashed or pre-filter-branch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
