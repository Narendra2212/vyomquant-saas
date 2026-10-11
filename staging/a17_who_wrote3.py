"""Phase 11B-A17. Attribution, attempt 3 -- deterministic patch construction.

Attempts 1 and 2 were INCONCLUSIVE: `git diff --no-index` emitted `a/a/<path>` headers, so
`apply` never addressed an index path and its "0 objects written" meant nothing. Rather than
fight the prefixes, take only the HUNK BODY from the diff and prepend correct headers.

The question remains: does `git apply --cached --check` with a private GIT_INDEX_FILE write
blobs into the SHARED object database? That decides whether the four new loose objects are
mine or a concurrent session's.
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

    tmp = tempfile.mkdtemp(prefix="a17_probe3_")
    try:
        af, bf = os.path.join(tmp, "old.yml"), os.path.join(tmp, "new.yml")
        for f, data in ((af, base), (bf, novel)):
            with open(f, "wb") as fh:
                fh.write(data)

        novel_sha = run(["git", "hash-object", bf]).stdout.strip()
        print(f"  probe post-image id : {novel_sha}")
        print(f"  present before test : {run(['git','cat-file','-e',novel_sha]).returncode == 0}")

        raw = run(["git", "diff", "--no-index", "-U3", "--", af, bf]).stdout
        hunks = raw[raw.index("@@"):] if "@@" in raw else ""
        assert hunks, "no hunks produced"
        patch = (
            f"diff --git a/{RELPATH} b/{RELPATH}\n"
            f"--- a/{RELPATH}\n"
            f"+++ b/{RELPATH}\n"
            f"{hunks}"
        )
        patchfile = os.path.join(tmp, "probe.patch")
        with open(patchfile, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(patch)
        print(f"  patch bytes={len(patch)}  hunks={patch.count('@@ -')}")

        before = loose()
        idx = os.path.join(tmp, "probe.index")
        env = {"GIT_INDEX_FILE": idx}
        print(f"  read-tree exit={run(['git','read-tree',PR6], env=env).returncode}")
        chk = run(["git", "apply", "--cached", "--check", patchfile], env=env)
        after_check = loose()
        print(f"  apply --cached --check exit={chk.returncode}")
        for l in (chk.stderr or "").splitlines()[:3]:
            print(f"      stderr: {l[:130]}")
        new_check = after_check - before
        stored_after_check = run(["git", "cat-file", "-e", novel_sha]).returncode == 0
        print(f"  objects created by --check : {len(new_check)} {sorted(s[:12] for s in new_check)}")
        print(f"  post-image stored          : {stored_after_check}")
        print()

        if chk.returncode != 0:
            print("  STILL INCONCLUSIVE -- patch rejected, so --check did no work.")
            return 2

        print("=== VERDICT ===")
        if stored_after_check or new_check:
            print("  `git apply --cached --check` WRITES blobs to the SHARED object database even")
            print("  with a private GIT_INDEX_FILE. The private index protects the INDEX only.")
            print("  => The four new loose objects are MY OWN doing. Hard constraint breached.")
        else:
            print("  `--check` wrote NOTHING on a valid, applicable patch.")
            print("  => My apply-checks are not the cause. Remaining explanation: a CONCURRENT")
            print("     session (known active: p11b17_* artifacts + a session-tagged A17 report")
            print("     appeared mid-phase) wrote them, most likely via `apply --cached` without")
            print("     --check or `hash-object -w` on the same three candidate files.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  temp dir removed: {not os.path.exists(tmp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
