"""Phase 11B-A17. Attribute the four new loose objects. Empirical, not speculative.

Four blobs appeared in .git/objects during this phase:
    20:24:40  57e2cee8  PR #6 workflow pre-image
    20:24:53  3ffbccde  webhook worktree candidate
    20:24:53  7baff0b6  metered worktree candidate
    20:26:06  967b4a28  rebuilt workflow candidate

My phase ran, among other things, `git apply --cached --check` against a PRIVATE index. The
question that decides attribution: DOES `git apply --cached --check` write blobs to the
shared object database even though the index it targets is private?

Test it directly. `967b4a28` is already present so it cannot re-prove anything; instead use
a blob that is certainly absent -- a trivially modified copy of a candidate -- and see whether
a --check run materialises it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
OBJ = os.path.join(REPO, ".git", "objects")


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=180
    )


def loose() -> set[str]:
    out = set()
    for root, _d, files in os.walk(OBJ):
        if "pack" in root or "info" in root:
            continue
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), OBJ).replace("\\", "/")
            parts = rel.split("/")
            if len(parts) == 2 and len(parts[0]) == 2:
                out.add(parts[0] + parts[1])
    return out


def main() -> int:
    print("=== EXPERIMENT: does `git apply --cached --check` write to the shared ODB? ===")
    # Build a patch whose post-image is certainly NOT in the store: take PR #6's workflow
    # and change the gate to an unused database index, /7.
    base = subprocess.run(
        ["git", "cat-file", "blob", f"{PR6}:.github/workflows/01-pr-check.yml"],
        cwd=REPO, capture_output=True,
    ).stdout
    novel = base.replace(b"redis://localhost:6379/15", b"redis://localhost:6379/7")
    assert novel != base, "probe substitution did not change anything"

    tmp = tempfile.mkdtemp(prefix="a17_probe_")
    try:
        # hash WITHOUT -w, outside the repo, to learn the id without storing it
        pf = os.path.join(tmp, "novel.yml")
        with open(pf, "wb") as fh:
            fh.write(novel)
        novel_sha = run(["git", "hash-object", pf]).stdout.strip()
        print(f"  probe post-image id : {novel_sha}")
        pre = run(["git", "cat-file", "-e", novel_sha])
        print(f"  present before test : {pre.returncode == 0}")

        # Build a real patch from PR#6 blob -> novel, via two temp files and git diff --no-index
        af = os.path.join(tmp, "a.yml")
        with open(af, "wb") as fh:
            fh.write(base)
        d = run(["git", "diff", "--no-index", "--", af, pf])
        patch = d.stdout.replace(af.replace("\\", "/"), "a/.github/workflows/01-pr-check.yml")
        patch = patch.replace(pf.replace("\\", "/"), "b/.github/workflows/01-pr-check.yml")
        patch = patch.replace(af, "a/.github/workflows/01-pr-check.yml")
        patch = patch.replace(pf, "b/.github/workflows/01-pr-check.yml")
        patchfile = os.path.join(tmp, "probe.patch")
        with open(patchfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(patch)

        before = loose()
        idx = os.path.join(tmp, "probe.index")
        env = {"GIT_INDEX_FILE": idx}
        run(["git", "read-tree", PR6], env=env)
        chk = run(["git", "apply", "--cached", "--check", patchfile], env=env)
        after = loose()
        print(f"  apply --cached --check exit={chk.returncode}")
        for l in (chk.stderr or "").splitlines()[:3]:
            print(f"      stderr: {l[:120]}")
        new = after - before
        print(f"  loose objects created by --check : {len(new)}  {sorted(s[:12] for s in new)}")
        post = run(["git", "cat-file", "-e", novel_sha])
        print(f"  probe post-image now stored     : {post.returncode == 0}")
        print()
        if post.returncode == 0 or new:
            print("  => `git apply --cached --check` DOES materialise blobs in the SHARED object")
            print("     database, even with a private GIT_INDEX_FILE. The private index protects")
            print("     the INDEX, not the object store. THIS IS THE CAUSE of the four objects.")
            print("     Attribution: MY OWN apply-check runs, not a concurrent session.")
        else:
            print("  => --check wrote nothing. The four objects are NOT attributable to it;")
            print("     a concurrent session is the remaining explanation.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  temp dir removed: {not os.path.exists(tmp)}")

    print()
    print("=== IMPACT ASSESSMENT ===")
    print("  Are the new objects REACHABLE from any ref (i.e. do they change repo content)?")
    for sha in (
        "967b4a28e29157f97f75d31a48eb9ce5b4133ce5",
        "3ffbccde68c3b57485a9971d4977c34aff598b47",
        "7baff0b64f0c7441f4192a9a6b2df53e7a68dce9",
        "57e2cee8ea448266efafa1441c2443a3c668e06e",
    ):
        r = run(["git", "rev-list", "--all", "--objects"])
        reachable = sha in r.stdout
        print(f"  {sha[:12]}  reachable_from_refs={reachable}")
    print()
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("origin/main", ["git", "rev-parse", "origin/main"]),
    ]:
        print(f"  {label:<12} {run(args).stdout.strip()}")
    n = len([l for l in run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines() if l.strip()])
    print(f"  staged paths {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
