"""Phase 11B-A17. READ-ONLY. Three objects appeared in .git/objects during this phase.

Entry measurement was 189 files; a later measurement is 192. The hard constraint was "no
shared Git-object writes", so this must be explained rather than glossed. Candidate causes:

  a) something I ran wrote objects despite the precautions;
  b) a concurrent session working this same repository wrote them (there is known concurrent
     D5' work -- p11b17_* artifacts and a session-tagged A17 report appeared mid-phase);
  c) git's own background maintenance (commit-graph, gc, pack).

Identify the new objects by mtime, type and content so the cause is attributable.
"""

from __future__ import annotations

import datetime
import os
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
OBJ = os.path.join(REPO, ".git", "objects")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace", timeout=120)


def main() -> int:
    rows = []
    for root, _dirs, files in os.walk(OBJ):
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, OBJ).replace("\\", "/")
            rows.append((st.st_mtime, st.st_size, rel))
    rows.sort(reverse=True)

    print(f"=== .git/objects: {len(rows)} files, 12 most recent ===")
    for mt, size, rel in rows[:12]:
        ts = datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")
        parts = rel.split("/")
        sha = (parts[0] + parts[1]) if len(parts) == 2 and len(parts[0]) == 2 else None
        kind = size_s = ""
        if sha:
            t = run(["git", "cat-file", "-t", sha])
            s = run(["git", "cat-file", "-s", sha])
            kind = t.stdout.strip()
            size_s = s.stdout.strip()
        print(f"  {ts}  {size:>8} B  {rel}")
        if sha:
            print(f"      sha={sha}  type={kind}  size={size_s}")
    print()

    print("=== loose objects newer than this phase's start (approx 20:0x) ===")
    cutoff = datetime.datetime.now().timestamp() - 3600  # last hour
    recent = [(mt, sz, rel) for mt, sz, rel in rows if mt >= cutoff]
    print(f"  count in the last hour: {len(recent)}")
    for mt, sz, rel in recent:
        ts = datetime.datetime.fromtimestamp(mt).strftime("%H:%M:%S")
        parts = rel.split("/")
        if len(parts) == 2 and len(parts[0]) == 2:
            sha = parts[0] + parts[1]
            t = run(["git", "cat-file", "-t", sha]).stdout.strip()
            print(f"  {ts}  {rel}  type={t}")
            if t == "blob":
                c = subprocess.run(["git", "cat-file", "blob", sha], cwd=REPO, capture_output=True)
                head = c.stdout[:200].decode("utf-8", errors="replace").replace("\n", "\\n")
                print(f"      first bytes: {head[:160]}")
                # Which known path, if any, does this match?
                for cand in (
                    "tests/test_webhook_duplicate_delivery_real_redis.py",
                    "tests/test_quota01_metered_reservation_is_hard.py",
                    ".github/workflows/01-pr-check.yml",
                ):
                    h = run(["git", "hash-object", cand]).stdout.strip()
                    if h == sha:
                        print(f"      *** MATCHES WORKTREE FILE: {cand} ***")
            else:
                print(f"      {run(['git','cat-file','-p',sha]).stdout[:300]}")
        else:
            print(f"  {ts}  {rel}  (not a loose object path)")
    print()

    print("=== is any CANDIDATE identifier now stored? (re-check) ===")
    for label, sha in [
        ("workflow candidate 967b4a28", "967b4a28e29157f97f75d31a48eb9ce5b4133ce5"),
        ("retired a2f39ebb", "a2f39ebbe76d4e75be7075080587c00d47094456"),
        ("webhook 3ffbccde", "3ffbccde68c3b57485a9971d4977c34aff598b47"),
        ("metered 7baff0b6", "7baff0b64f0c7441f4192a9a6b2df53e7a68dce9"),
    ]:
        r = run(["git", "cat-file", "-e", sha])
        print(f"  {label:<30} {'STORED' if r.returncode == 0 else 'still computed-only'}")
    print()

    print("=== shared refs / index unchanged? ===")
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("staged count", ["git", "diff", "--cached", "--name-only"]),
    ]:
        out = run(args).stdout
        val = out.strip() if label == "HEAD" else str(len([l for l in out.splitlines() if l.strip()]))
        print(f"  {label:<14} {val}")
    r = run(["git", "fsck", "--connectivity-only", "--no-progress"])
    print(f"  git fsck exit={r.returncode}")
    for l in (r.stdout + r.stderr).splitlines()[:8]:
        print(f"      {l[:130]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
