"""Phase 11B-A17. READ-ONLY. Independently recompute the three-file candidate.

Nothing here is taken on trust from A15, A16 or A16R. In particular the workflow candidate
is REBUILT in this phase from the CURRENT PR #6 blob, because two incompatible values are on
record:

    A15  a2f39ebb...   (A16R reports this as non-reproducible)
    A16R 967b4a28...   (rebuilt from 57e2cee8 + the documented regex)

`git hash-object` is used WITHOUT `-w`, so no object enters the shared database. The rebuilt
content lives in a temp file outside the repository and is deleted.

Also verifies A16's claim that PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md contains no
PR-targeting language -- the basis on which request B is treated as resolved.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"

MANIFEST = {
    ".github/workflows/01-pr-check.yml": "57e2cee8",
    "tests/test_webhook_duplicate_delivery_real_redis.py": "105b6567",
    "tests/test_quota01_metered_reservation_is_hard.py": "f48103ac",
}

RECORDED_POST = {
    "tests/test_webhook_duplicate_delivery_real_redis.py": "3ffbccde68c3b57485a9971d4977c34aff598b47",
    "tests/test_quota01_metered_reservation_is_hard.py": "7baff0b64f0c7441f4192a9a6b2df53e7a68dce9",
}

GATE_RE = re.compile(r"(^\s*AERORA_TEST_REDIS_URL:\s*redis://localhost:6379)/15\s*$", re.M)


def run(args: list[str], cwd: str = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace")


def blob_bytes(rev: str, path: str) -> bytes:
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def hash_object_nowrite(data: bytes) -> str:
    """git hash-object on a temp file OUTSIDE the repo. No -w, so nothing is stored."""
    fd, tmp = tempfile.mkstemp(prefix="a17_cand_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        r = subprocess.run(
            ["git", "hash-object", tmp], cwd=REPO, capture_output=True, text=True, errors="replace"
        )
        return r.stdout.strip()
    finally:
        os.unlink(tmp)
        assert not os.path.exists(tmp), "temp candidate file was not removed"


def main() -> int:
    print("=== PRE-IMAGES: current PR #6 blobs ===")
    pre = {}
    for path, expected in MANIFEST.items():
        r = run(["git", "rev-parse", f"{PR6}:{path}"])
        pre[path] = r.stdout.strip()
        ok = pre[path].startswith(expected)
        print(f"  {path}")
        print(f"      PR#6 blob {pre[path]}   expected {expected}..  {'MATCH' if ok else 'MISMATCH'}")
    print()

    print("=== POST-IMAGES: the two test files (worktree), re-hashed now ===")
    for path, expected in RECORDED_POST.items():
        r = run(["git", "hash-object", path])
        actual = r.stdout.strip()
        print(f"  {path}")
        print(f"      recorded {expected}")
        print(f"      measured {actual}   {'MATCH' if actual == expected else 'DRIFTED'}")
    print()

    print("=== WORKFLOW CANDIDATE: rebuilt in THIS phase from current PR #6 ===")
    wf6 = blob_bytes(PR6, ".github/workflows/01-pr-check.yml")
    print(f"  base blob      : {pre['.github/workflows/01-pr-check.yml']}")
    print(f"  base bytes     : {len(wf6)}   lines={wf6.count(bytes([10]))}")
    print(f"  base has CRLF  : {bytes([13,10]) in wf6}")

    text = wf6.decode("utf-8")
    hits = [m.group(0).strip() for m in GATE_RE.finditer(text)]
    print(f"  /15 env lines found: {len(hits)}")
    for h in hits:
        print(f"      {h}")
    rebuilt_text, nsub = GATE_RE.subn(r"\1/0", text)
    rebuilt = rebuilt_text.encode("utf-8")
    print(f"  substitutions made : {nsub}")

    cand = hash_object_nowrite(rebuilt)
    print(f"  REBUILT CANDIDATE  : {cand}")
    print(f"      vs A16R 967b4a28e29157f97f75d31a48eb9ce5b4133ce5 : "
          f"{cand == '967b4a28e29157f97f75d31a48eb9ce5b4133ce5'}")
    print(f"      vs A15  a2f39ebbe76d4e75be7075080587c00d47094456 : "
          f"{cand == 'a2f39ebbe76d4e75be7075080587c00d47094456'}")
    print()

    print("=== WORKFLOW SEMANTIC REQUIREMENTS (on the rebuilt candidate) ===")
    env_all = re.findall(r"^\s*AERORA_TEST_REDIS_URL:\s*(\S+)\s*$", rebuilt_text, re.M)
    print(f"  AERORA_TEST_REDIS_URL values     : {env_all}")
    print(f"  count targeting /0               : {sum(1 for v in env_all if v.endswith('/0'))}")
    print(f"  count still on /15               : {sum(1 for v in env_all if v.endswith('/15'))}")
    print(f"  d5-redis-diagnostics retained    : {'d5-redis-diagnostics' in rebuilt_text}")
    print(f"  provisioning-and-isolation absent: {'provisioning-and-isolation' not in rebuilt_text}")
    print(f"  bytes changed vs base            : {len(rebuilt) - len(wf6)} (expect -2, '/15'->'/0' twice)")
    print(f"  line count unchanged             : {rebuilt.count(bytes([10])) == wf6.count(bytes([10]))}")
    only = [
        (i + 1, a, b)
        for i, (a, b) in enumerate(zip(text.splitlines(), rebuilt_text.splitlines()))
        if a != b
    ]
    print(f"  differing lines                  : {len(only)}")
    for ln, a, b in only:
        print(f"      L{ln}: {a.strip()}  ->  {b.strip()}")
    print()

    print("=== A16's BASIS FOR REQUEST B: does A12 contain PR-targeting language? ===")
    a12 = os.path.join(REPO, "staging", "PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md")
    txt = open(a12, encoding="utf-8", errors="replace").read()
    refs = [(i, l.strip()) for i, l in enumerate(txt.splitlines(), 1) if re.search(r"PR\s*#\s*\d", l)]
    print(f"  PR references in A12: {len(refs)}")
    for i, l in refs:
        print(f"      L{i}: {l[:150]}")
    targeting = [
        (i, l) for i, l in refs
        if re.search(r"canonical|target|must land|should land|only on|prefer", l, re.I)
    ]
    print(f"  of those, expressing a TARGET/canonical preference: {len(targeting)}")
    for i, l in targeting:
        print(f"      L{i}: {l[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
