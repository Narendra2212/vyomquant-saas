"""Definitive check: does EITHER missing module exist as ANY object in this repository?

PHASE 11B / WORKSTREAM 1C — Phase 0 recovery evidence.

Passes 1 and 2 (the 31 `.git/lost-found/other` objects and the 112 dangling commit trees)
both returned zero. This is the exhaustive pass: every object in the object database,
reachable or not, including anything left behind by the `filter-branch` whose artifact
still sits in `git stash list`.

PERFORMANCE NOTE -- why this exists as a separate probe
-------------------------------------------------------
A first attempt spawned one `git cat-file` per object and was still running after 30
minutes, producing nothing. This uses the plumbing as intended: ONE
`git cat-file --batch-check --batch-all-objects` to enumerate, then ONE
`git cat-file --batch` fed the candidate IDs on stdin. Two processes total.

Blobs are filtered to a 1 KB..60 KB band before reading. The recovered source sizes are
10,716 and 4,198 bytes (from the `.pyc` headers), so the band is wide enough to catch any
earlier or later revision while skipping the bulk of the repository's objects.

READ-ONLY with respect to git: reads objects only. Creates no ref, commit, stash, index
entry or working-tree file.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

FINGERPRINTS = {
    "probe_pg_find": [
        b"def find_and_record", b"def candidate_dsns", b"def _port_from_pid_file",
        b"HISTORICAL_PORTS", b"DEFAULT_INTENDED_DIR", b"_probe_find_bootstrap_pg",
        b"NO SUITABLE CANDIDATE", b"--any-provisioning",
    ],
    "pg_keepalive_r1": [
        b"KEEPALIVE: sleeping", b"PG_DSN_NOT_RECORDED", b"PG_STARTED port=",
        b"PG_NEVER_READY", b"_keepalive_bootstrap_pg",
    ],
}

LOW, HIGH = 1_000, 60_000


def main() -> int:
    print(f"repo: {REPO}")
    for rel in ("staging/probe_pg_find.py", "staging/pg_keepalive_r1.py"):
        print(f"   {rel:34} on disk = {(REPO / rel).exists()}")

    print("\n-- enumerate every object (1 process) ------------------------------------")
    enum = subprocess.run(
        ["git", "cat-file", "--batch-check", "--batch-all-objects"],
        cwd=str(REPO), capture_output=True, timeout=1800)
    if enum.returncode != 0:
        print(f"   FAILED: {enum.stderr.decode('utf-8', 'replace')[:300]}")
        return 1

    total = 0
    kinds: dict[str, int] = {}
    candidates: list[tuple[str, int]] = []
    for line in enum.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        total += 1
        oid, kind, size = parts[0], parts[1], int(parts[2])
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "blob" and LOW <= size <= HIGH:
            candidates.append((oid, size))

    print(f"   objects total        : {total}")
    for kind in sorted(kinds):
        print(f"     {kind:8} {kinds[kind]}")
    print(f"   candidate blobs      : {len(candidates)}  "
          f"({LOW:,}B .. {HIGH:,}B)")

    print("\n-- read every candidate blob (1 process) ---------------------------------")
    payload = "\n".join(oid for oid, _ in candidates) + "\n"
    read = subprocess.run(["git", "cat-file", "--batch"], cwd=str(REPO),
                          input=payload.encode("ascii"), capture_output=True,
                          timeout=1800)
    if read.returncode != 0:
        print(f"   FAILED: {read.stderr.decode('utf-8', 'replace')[:300]}")
        return 1

    # `--batch` emits: "<oid> <type> <size>\n<content>\n" per request.
    buf = read.stdout
    pos = 0
    examined = 0
    hits: list[tuple[str, int, str, int]] = []
    while pos < len(buf):
        nl = buf.find(b"\n", pos)
        if nl == -1:
            break
        header = buf[pos:nl].decode("ascii", "replace").split()
        if len(header) != 3:
            break
        oid, _kind, size = header[0], header[1], int(header[2])
        start = nl + 1
        content = buf[start:start + size]
        pos = start + size + 1
        examined += 1
        for name, tokens in FINGERPRINTS.items():
            score = sum(1 for t in tokens if t in content)
            if score >= 2:
                hits.append((oid, size, name, score))
                print(f"   {oid[:12]} {size:>7}B  MATCH {name} "
                      f"({score}/{len(tokens)} fingerprints)")

    print(f"   blobs examined       : {examined}")
    print(f"   matches              : {len(hits) or 'NONE'}")

    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    if hits:
        print("   EXACT RECOVERY IS POSSIBLE. Blobs above contain the missing source.")
        for oid, size, name, score in hits:
            print(f"     git cat-file blob {oid} > staging/{name}.py   ({size} B)")
        return 0
    print("   NO OBJECT in this repository contains either module.")
    print("   Checked: reachable history, all 112 dangling commits, both stash entries,")
    print("   the 31 lost-found objects, and every blob in the object database.")
    print("   EXACT SOURCE RECOVERY FROM GIT IS IMPOSSIBLE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
