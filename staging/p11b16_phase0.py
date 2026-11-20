"""Phase 11B-A16 Phase 0: batched read-only rebaseline + artifact/approval survey."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

A15 = {
    "head": "074b772ab16cece62cbfca0f2b78497be2ae56d5",
    "index_content": "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d",
    "staged": 22,
    "prs": {6: "d07014d36d32c63db44f8fb9659933c0e84e82f2",
            7: "49e70da23c2858ffc9a6f34d5db4521401477c82",
            8: "a624ce89b16005b0c73197005d6d0672737dad65"},
    # pre-image blobs the prepared patch is expressed against (PR #6 side)
    "patch_pre": {".github/workflows/01-pr-check.yml": "57e2cee8",
                  "tests/test_webhook_duplicate_delivery_real_redis.py": "105b6567",
                  "tests/test_quota01_metered_reservation_is_hard.py": "f48103ac"},
    # worktree blobs the patch carries as the post-image
    "patch_post": {".github/workflows/01-pr-check.yml": "967b4a28",
                   "tests/test_webhook_duplicate_delivery_real_redis.py": "3ffbccde",
                   "tests/test_quota01_metered_reservation_is_hard.py": "7baff0b6"},
}

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def flag(now, then):
    return "SAME" if now == then else ">> CHANGED"


# ---------------------------------------------------------------- A. git state
out.append("=" * 94)
out.append("A. GIT STATE vs A15")
out.append("=" * 94)
head = sh(["git", "rev-parse", "HEAD"]).strip()
out.append("  %-10s HEAD                 %s" % (flag(head, A15["head"]), head))
out.append("  %-10s origin/main          %s"
           % (flag(sh(["git", "rev-parse", "origin/main"]).strip(), A15["head"]),
              sh(["git", "rev-parse", "origin/main"]).strip()))
out.append("             branch               %s" % sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())

stage_lines = sorted(sh(["git", "ls-files", "--stage"]).splitlines())
h = hashlib.sha256()
for l in stage_lines:
    h.update(l.encode("utf-8"))
con = h.hexdigest()
out.append("  %-10s index CONTENT digest %s" % (flag(con, A15["index_content"]), con))

staged = [l for l in sh(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("  %-10s staged paths         %d" % (flag(len(staged), A15["staged"]), len(staged)))
unstaged = [l for l in sh(["git", "diff", "--name-only"]).splitlines() if l.strip()]
untracked = [l for l in sh(["git", "status", "--porcelain"]).splitlines() if l.startswith("??")]
out.append("             worktree: %d unstaged-modified, %d untracked (dir-collapsed)"
           % (len(unstaged), len(untracked)))
unmerged = [l for l in sh(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]
out.append("             unmerged entries: %d %s" % (len(unmerged), "OK" if not unmerged else ">> CONFLICTS"))

out.append("")
out.append("  staged paths:")
for p in staged:
    out.append("    %s" % p)

# ---------------------------------------------------------------- B. protected files
out.append("")
out.append("=" * 94)
out.append("B. PROTECTED / NON-TOUCH FILES")
out.append("=" * 94)
for p in [".gitignore", "backend_app/core/subscription_dependencies.py",
          "backend_app/routers/exchange.py", "tests/test_quota01_exchange_slot_is_atomic.py"]:
    st = sh(["git", "status", "--porcelain", "--", p]).rstrip()
    num = sh(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    delta = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    out.append("  %-54s %-5s %s" % (p, st[:2] if st else "  ", delta))

# ---------------------------------------------------------------- C. patch manifest
out.append("")
out.append("=" * 94)
out.append("C. PATCH MANIFEST -- do the three candidate blobs still match?")
out.append("=" * 94)
for path, pre in A15["patch_pre"].items():
    real_pre = sh(["git", "rev-parse", "%s:%s" % (A15["prs"][6], path)]).strip()[:8]
    post = sh(["git", "hash-object", path]).strip()[:8]
    exp_post = A15["patch_post"][path]
    out.append("  %s" % path)
    out.append("    %-10s PR#6 pre-image  %s (patch expects %s)" % (flag(real_pre, pre), real_pre, pre))
    out.append("    %-10s worktree post   %s (patch expects %s)" % (flag(post, exp_post), post, exp_post))

# ---------------------------------------------------------------- D. artifacts
out.append("")
out.append("=" * 94)
out.append("D. A15 ARTIFACTS AND OUTPUT DESTINATION")
out.append("=" * 94)
for f in ["staging/PHASE11B_A15_D5_EXECUTION_REPORT.md",
          "staging/p11b15_proposed_fixed.patch",
          "staging/p11b15_proposed.patch",
          "staging/p11b15_approval_search.txt",
          "staging/p11b15_focused.xml",
          "staging/PHASE11B_A14_D5_CONSOLIDATION_REPORT.md",
          "staging/PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md",
          "staging/PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md",
          "staging/PHASE11B_A13_REAL_REDIS_VERIFICATION.md",
          "staging/PHASE11B_A13_D5_REPAIR_REPORT.md",
          "staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md"]:
    p = Path(f)
    if p.exists():
        out.append("  PRESENT  %-62s %7d bytes  mtime=%s"
                   % (f, p.stat().st_size,
                      __import__("datetime").datetime.fromtimestamp(p.stat().st_mtime)
                      .strftime("%Y-%m-%d %H:%M:%S")))
    else:
        out.append("  ABSENT   %s" % f)

# ---------------------------------------------------------------- E. PR state
out.append("")
out.append("=" * 94)
out.append("E. PR STATE AND TREE IDENTITY")
out.append("=" * 94)
prs = json.loads(sh(["gh", "pr", "list", "--state", "all", "--limit", "30", "--json",
                     "number,state,headRefName,headRefOid,baseRefName,mergedAt,reviewDecision,"
                     "reviews,labels"]) or "[]")
by = {p["number"]: p for p in prs}
for n, then in A15["prs"].items():
    p = by.get(n)
    if not p:
        out.append("  >> PR #%d MISSING" % n)
        continue
    tree = sh(["git", "rev-parse", "%s^{tree}" % p["headRefOid"]]).strip()
    out.append("  %-10s PR #%d head %s  state=%s merged=%s"
               % (flag(p["headRefOid"], then), n, p["headRefOid"][:12], p["state"], p["mergedAt"]))
    out.append("             tree=%s  reviewDecision=%r  reviews=%d  labels=%s"
               % (tree[:12], p.get("reviewDecision"), len(p.get("reviews") or []),
                  [l["name"] for l in p.get("labels", [])]))
t6 = sh(["git", "rev-parse", "%s^{tree}" % A15["prs"][6]]).strip()
t7 = sh(["git", "rev-parse", "%s^{tree}" % A15["prs"][7]]).strip()
t8 = sh(["git", "rev-parse", "%s^{tree}" % A15["prs"][8]]).strip()
out.append("")
out.append("  tree identity: #7 == #8 ? %s      #6 == #7 ? %s" % (t7 == t8, t6 == t7))

text = "\n".join(out)
open("staging/p11b16_phase0.txt", "w", encoding="utf-8").write(text)
print(text)
