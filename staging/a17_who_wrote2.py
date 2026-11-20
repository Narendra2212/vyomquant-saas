"""Phase 11B-A17. Attribution, attempt 2. The first probe built an INVALID patch
("does not exist in index"), so --check never ran and its "0 objects" proved nothing.

Build a VALID patch this time: mirror the real repo path under a temp root so
`git diff --no-index` emits `a/<real path>` and `b/<real path>` headers, then run
`git apply --cached --check` against a private index seeded from PR #6.

Decisive because the post-image (/15 -> /7) is certainly not in the object store.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
OBJ = os.path.join(REPO, ".git", "objects")
RELPATH = ".github/workflows/01-pr-check.yml"


def run(args: list[str], cwd: str = REPO, env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, errors="replace", env=e, timeout=180
    )


def loose() -> set[str]:
    out = set()
    for root, _d, files in os.walk(OBJ):
        if "pack" in root or "info" in root:
            continue
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), OBJ).replace("\\", "/")
            p = rel.split("/")
            if len(p) == 2 and len(p[0]) == 2:
                out.add(p[0] + p[1])
    return out


def main() -> int:
    base = subprocess.run(
        ["git", "cat-file", "blob", f"{PR6}:{RELPATH}"], cwd=REPO, capture_output=True
    ).stdout
    novel = base.replace(b"redis://localhost:6379/15", b"redis://localhost:6379/7")
    assert novel != base

    tmp = tempfile.mkdtemp(prefix="a17_probe2_")
    try:
        # mirror the real path under a/ and b/ so diff headers are repo-relative
        for side, data in (("a", base), ("b", novel)):
            d = os.path.join(tmp, side, os.path.dirname(RELPATH))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(tmp, side, RELPATH), "wb") as fh:
                fh.write(data)

        # hash the novel post-image WITHOUT -w to learn its id
        novel_sha = run(["git", "hash-object", os.path.join(tmp, "b", RELPATH)]).stdout.strip()
        print(f"  probe post-image id : {novel_sha}")
        print(f"  present before test : {run(['git','cat-file','-e',novel_sha]).returncode == 0}")

        d = run(
            ["git", "diff", "--no-index", "--", f"a/{RELPATH}", f"b/{RELPATH}"], cwd=tmp
        )
        patch = d.stdout
        patchfile = os.path.join(tmp, "probe.patch")
        with open(patchfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(patch)
        print(f"  patch bytes={len(patch)}  header={patch.splitlines()[0][:90] if patch else '<empty>'}")

        before = loose()
        idx = os.path.join(tmp, "probe.index")
        env = {"GIT_INDEX_FILE": idx}
        seed = run(["git", "read-tree", PR6], env=env)
        chk = run(["git", "apply", "--cached", "--check", patchfile], env=env)
        after = loose()
        print(f"  read-tree exit={seed.returncode}")
        print(f"  apply --cached --check exit={chk.returncode}")
        for l in (chk.stderr or "").splitlines()[:3]:
            print(f"      stderr: {l[:130]}")
        new = after - before
        stored_now = run(["git", "cat-file", "-e", novel_sha]).returncode == 0
        print(f"  loose objects created : {len(new)} {sorted(s[:12] for s in new)}")
        print(f"  post-image stored now : {stored_now}")
        print()

        if chk.returncode != 0 and "does not exist in index" in (chk.stderr or ""):
            print("  INCONCLUSIVE AGAIN: the patch still did not address an index path.")
            return 2
        if stored_now or new:
            print("  RESULT: `git apply --cached --check` DOES write blobs to the SHARED object")
            print("  store even with a private GIT_INDEX_FILE. A private index protects the index,")
            print("  NOT the object database. The four objects are MY OWN doing.")
        else:
            print("  RESULT: `--check` wrote nothing on a valid patch. My apply-checks are")
            print("  therefore NOT the cause; a concurrent session wrote the four objects.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  temp dir removed: {not os.path.exists(tmp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
