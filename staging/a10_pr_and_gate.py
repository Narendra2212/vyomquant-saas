"""Phase 11B-A10. READ-ONLY. PR comparison and approval-gate state.

Two questions:
  1. Are PR #7 and #8 STILL identical? The brief says confirm before recommending that a
     duplicate be closed -- a stale "they're the same" would make closing one lossy.
  2. Is any approval present? A15 found items A-E all absent. Re-check, because an approval
     could have arrived since.

`gh` is used read-only (pr view / pr list). If it is unauthenticated, that is reported as
"cannot determine" rather than silently treated as "no approval".
"""

from __future__ import annotations

import json
import shutil
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

PR = {
    "#6": ("d07014d36d32c63db44f8fb9659933c0e84e82f2", "ci/d5-real-redis-quota-evidence"),
    "#7": ("49e70da23c2858ffc9a6f34d5db4521401477c82", "ci/d5-prime-real-redis"),
    "#8": ("a624ce89b16005b0c73197005d6d0672737dad65", "ci/d5-real-redis-switch"),
}


def run(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, cwd=REPO, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "<timeout>")
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", "<not found>")


def main() -> int:
    print("=== 1. PR TREE IDENTITY, re-verified now ===")
    trees = {}
    for label, (sha, branch) in PR.items():
        r = run(["git", "rev-parse", f"{sha}^{{tree}}"])
        trees[label] = r.stdout.strip()
        print(f"  PR {label}  {sha[:10]}  {branch:<34} tree={trees[label]}")
    same = trees["#7"] == trees["#8"]
    print(f"  #7 tree == #8 tree : {same}")
    print(f"  #6 tree distinct   : {trees['#6'] != trees['#7']}")
    d = run(["git", "diff", "--stat", PR["#7"][0], PR["#8"][0]])
    print(f"  git diff #7..#8    : exit={d.returncode} out={d.stdout.strip() or '(empty)'}")
    if same and not d.stdout.strip():
        print("  => STILL identical. Closing exactly one of #7/#8 loses nothing.")
    else:
        print("  => NO LONGER identical. Re-assess before closing either.")
    print()

    print("=== 2. REMOTE HEADS (do local shas still match the remote?) ===")
    r = run(["git", "ls-remote", "--heads", "origin"], timeout=120)
    if r.returncode != 0:
        print(f"  ls-remote failed exit={r.returncode} {r.stderr.strip()[:120]}")
    else:
        remote = {}
        for line in r.stdout.splitlines():
            if "\t" not in line:
                continue
            sha, ref = line.split("\t", 1)
            remote[ref.strip().replace("refs/heads/", "")] = sha.strip()
        for label, (sha, branch) in PR.items():
            rs = remote.get(branch, "<absent>")
            print(f"  {branch:<34} remote={rs[:10]}  expected={sha[:10]}  match={rs == sha}")
        print(f"  {'main':<34} remote={remote.get('main','<absent>')[:10]}")
    print()

    print("=== 3. APPROVAL GATE ===")
    if not shutil.which("gh"):
        print("  gh not on PATH -> approval state CANNOT BE DETERMINED from here.")
        return 0
    auth = run(["gh", "auth", "status"])
    print(f"  gh auth status exit={auth.returncode}")
    blob = (auth.stdout + auth.stderr).strip()
    for line in blob.splitlines()[:6]:
        print(f"    {line[:110]}")
    if auth.returncode != 0:
        print("  => NOT AUTHENTICATED. Approval state cannot be read; treat as UNDETERMINED,")
        print("     which for gating purposes is the same as ABSENT: do not proceed.")
        return 0

    for label in ("6", "7", "8"):
        r = run(
            [
                "gh", "pr", "view", label,
                "--json", "number,state,isDraft,mergedAt,reviewDecision,reviews,labels,assignees,comments,mergeStateStatus,baseRefName,headRefName,headRefOid",
            ],
            timeout=120,
        )
        if r.returncode != 0:
            print(f"  PR #{label}: query failed exit={r.returncode} {r.stderr.strip()[:110]}")
            continue
        try:
            d = json.loads(r.stdout)
        except Exception as exc:  # noqa: BLE001
            print(f"  PR #{label}: unparseable ({type(exc).__name__})")
            continue
        reviews = d.get("reviews") or []
        comments = d.get("comments") or []
        authors = sorted({(c.get("author") or {}).get("login", "?") for c in comments})
        print(f"  PR #{d.get('number')}  state={d.get('state')}  draft={d.get('isDraft')}  merged={d.get('mergedAt')}")
        print(f"      base={d.get('baseRefName')}  head={d.get('headRefName')} @ {str(d.get('headRefOid'))[:10]}")
        print(f"      mergeStateStatus={d.get('mergeStateStatus')}  reviewDecision={d.get('reviewDecision')!r}")
        print(f"      reviews={len(reviews)}  labels={len(d.get('labels') or [])}  assignees={len(d.get('assignees') or [])}")
        print(f"      comments={len(comments)} by {authors}")
        states = [rv.get("state") for rv in reviews]
        print(f"      review states={states}")
        approved = [s for s in states if s == "APPROVED"]
        print(f"      APPROVED reviews={len(approved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
