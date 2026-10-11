"""Phase 11B-A17. READ-ONLY. Patch applicability + stored-object verification.

Two things:

  1. Does `staging/p11b15_proposed_fixed.patch` still apply CLEANLY to the CURRENT PR #6
     head? Tested with a disposable private index seeded from d07014d3, check-only.
     The shared index is never named; its invariant is re-measured afterwards.

  2. Are the quoted identifiers stored objects, or only computed digests? The existing A17
     claims the worktree ones are computed-only. `git cat-file -e` answers it. This matters
     because "blob 3ffbccde" reads as though an object exists that a committer could
     reference -- it does not, and the content must come from the worktree or the patch.

Nothing is written with `-w`. No candidate object enters the shared database.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"

PATCHES = [
    "staging/p11b15_proposed_fixed.patch",
    "staging/p11b15_proposed.patch",
    "staging/p11b15_wf_only.patch",
]

IDENTIFIERS = [
    ("PR#6 workflow (stored expected)", "57e2cee8ea448266efafa1441c2443a3c668e06e"),
    ("PR#6 webhook  (stored expected)", "105b65671b279efb958905c718e1831719ad26ee"),
    ("PR#6 metered  (stored expected)", "f48103ac79a2b7adf76339aab0f2861168b1defc"),
    ("workflow candidate 967b4a28", "967b4a28e29157f97f75d31a48eb9ce5b4133ce5"),
    ("retired candidate a2f39ebb", "a2f39ebbe76d4e75be7075080587c00d47094456"),
    ("webhook worktree 3ffbccde", "3ffbccde68c3b57485a9971d4977c34aff598b47"),
    ("metered worktree 7baff0b6", "7baff0b64f0c7441f4192a9a6b2df53e7a68dce9"),
    ("gate d40a1bb7", "d40a1bb7766307f067d914ebc8e6fd705e04a222"),
    ("slot test 2d7ccbbb", "2d7ccbbb04b3af04675b365809d89b4429db4888"),
]


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=180
    )


def shared_invariants() -> tuple[str, str, int]:
    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    di = run(["git", "diff-index", "--cached", "HEAD"])
    rows = [l for l in di.stdout.splitlines() if l.strip()]
    c = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
    return a, c, len(rows)


def objects_count() -> int:
    return sum(len(f) for _, _, f in os.walk(os.path.join(REPO, ".git", "objects")))


def main() -> int:
    a0, c0, n0 = shared_invariants()
    obj0 = objects_count()
    print("=== SHARED INVARIANTS BEFORE ===")
    print(f"  digest A {a0}")
    print(f"  digest C {c0}   staged={n0}")
    print(f"  .git/objects files {obj0}")
    print()

    print("=== 1. PATCH APPLICABILITY vs CURRENT PR #6 (private index, --check only) ===")
    tmpdir = tempfile.mkdtemp(prefix="a17_idx_")
    try:
        for patch in PATCHES:
            full = os.path.join(REPO, patch.replace("/", os.sep))
            if not os.path.exists(full):
                print(f"  {patch}: ABSENT")
                continue
            size = os.path.getsize(full)
            idx = os.path.join(tmpdir, os.path.basename(patch) + ".index")
            env = {"GIT_INDEX_FILE": idx}
            seed = run(["git", "read-tree", PR6], env=env)
            chk = run(["git", "apply", "--cached", "--check", patch], env=env)
            print(f"  {patch}  ({size} B)")
            print(f"      seed read-tree {PR6[:10]} exit={seed.returncode}")
            print(f"      apply --cached --check exit={chk.returncode} -> "
                  f"{'APPLIES CLEANLY' if chk.returncode == 0 else 'REJECTED'}")
            for line in (chk.stderr or "").splitlines()[:4]:
                print(f"      stderr: {line[:130]}")
            # pre-image index lines declared by the patch itself
            with open(full, encoding="utf-8", errors="replace") as fh:
                for l in fh:
                    if l.startswith("index "):
                        print(f"      declares: {l.strip()[:80]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print(f"  private index dir removed: {not os.path.exists(tmpdir)}")
    print()

    print("=== 2. STORED OBJECT vs COMPUTED DIGEST (git cat-file -e) ===")
    for label, sha in IDENTIFIERS:
        r = run(["git", "cat-file", "-e", sha])
        stored = r.returncode == 0
        print(f"  {label:<34} {sha[:12]}  {'STORED' if stored else 'computed-only (absent)'}")
    print()
    print("  Reading: a 'computed-only' identifier is a content fingerprint. No committer can")
    print("  reference it; the content must be supplied from the worktree or the patch. That is")
    print("  also positive evidence that nothing was ever staged or hash-objected with -w.")
    print()

    a1, c1, n1 = shared_invariants()
    obj1 = objects_count()
    print("=== SHARED INVARIANTS AFTER ===")
    print(f"  digest A {a1}   unchanged={a0 == a1}")
    print(f"  digest C {c1}   unchanged={c0 == c1}   staged {n0} -> {n1}")
    print(f"  .git/objects files {obj0} -> {obj1}   delta={obj1 - obj0}")
    ok = a0 == a1 and c0 == c1 and n0 == n1 and obj0 == obj1
    print(f"  ALL SHARED INVARIANTS HELD: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
