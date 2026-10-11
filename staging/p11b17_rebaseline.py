"""Phase 11B-A17 step 1: batched read-only rebaseline vs A16. Writes nothing but its own log."""
import hashlib
import json
import subprocess
from pathlib import Path

A16 = {
    "head": "074b772ab16cece62cbfca0f2b78497be2ae56d5",
    "index_content": "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d",
    "staged": 22,
    "prs": {6: ("d07014d36d32c63db44f8fb9659933c0e84e82f2", "d28b6d1a5d61"),
            7: ("49e70da23c2858ffc9a6f34d5db4521401477c82", "271f13c29d3c"),
            8: ("a624ce89b16005b0c73197005d6d0672737dad65", "271f13c29d3c")},
    "protected": {
        ".gitignore": "+15/-0",
        "backend_app/core/subscription_dependencies.py": "+253/-0",
        "backend_app/routers/exchange.py": "+422/-29",
        "tests/test_quota01_exchange_slot_is_atomic.py": "clean",
    },
    "patch_pre": {".github/workflows/01-pr-check.yml": "57e2cee8",
                  "tests/test_webhook_duplicate_delivery_real_redis.py": "105b6567",
                  "tests/test_quota01_metered_reservation_is_hard.py": "f48103ac"},
    "worktree": {".github/workflows/01-pr-check.yml": "91d55a37",
                 "tests/test_webhook_duplicate_delivery_real_redis.py": "3ffbccde",
                 "tests/test_quota01_metered_reservation_is_hard.py": "7baff0b6"},
    "patch_fixed_bytes": 34947,
    "patch_orig_bytes": 35695,
}

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def f(now, then):
    return "SAME" if str(now) == str(then) else ">> CHANGED"


out.append("=" * 96)
out.append("A. GIT STATE vs A16")
out.append("=" * 96)
head = sh(["git", "rev-parse", "HEAD"]).strip()
om = sh(["git", "rev-parse", "origin/main"]).strip()
out.append("  %-11s HEAD                  %s" % (f(head, A16["head"]), head))
out.append("  %-11s origin/main           %s" % (f(om, A16["head"]), om))
out.append("              branch                %s" % sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())

stage = sorted(sh(["git", "ls-files", "--stage"]).splitlines())
h = hashlib.sha256()
for l in stage:
    h.update(l.encode("utf-8"))
con = h.hexdigest()
out.append("  %-11s index CONTENT digest  %s" % (f(con, A16["index_content"]), con))
out.append("              (A16 method: sha256 over sorted `git ls-files --stage` lines)")

staged = [l for l in sh(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("  %-11s staged paths          %d" % (f(len(staged), A16["staged"]), len(staged)))
unstaged = [l for l in sh(["git", "diff", "--name-only"]).splitlines() if l.strip()]
untracked = [l for l in sh(["git", "status", "--porcelain"]).splitlines() if l.startswith("??")]
unmerged = [l for l in sh(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]
out.append("              unstaged-modified     %d" % len(unstaged))
out.append("              untracked (collapsed) %d" % len(untracked))
out.append("  %-11s unmerged entries      %d" % ("SAME" if not unmerged else ">> CONFLICTS", len(unmerged)))

out.append("")
out.append("  NOTE ON `git write-tree`: deliberately NOT run. It writes tree objects into the")
out.append("  shared object database and can rewrite the shared index cache-tree, both of which")
out.append("  this phase's safety constraints forbid. The A16-method content digest above is the")
out.append("  authoritative invariant; see the report for the flagged instruction conflict.")

out.append("")
out.append("=" * 96)
out.append("B. PROTECTED FILES vs A16")
out.append("=" * 96)
for p, then in A16["protected"].items():
    st = sh(["git", "status", "--porcelain", "--", p]).rstrip()
    num = sh(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    now = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    blob = sh(["git", "hash-object", p]).strip()[:8]
    out.append("  %-11s %-52s %-5s %-10s blob=%s" % (f(now, then), p, st[:2] if st else "  ", now, blob))

out.append("")
out.append("=" * 96)
out.append("C. PR STATE vs A16 (head SHA, tree SHA, review, checks)")
out.append("=" * 96)
prs = json.loads(sh(["gh", "pr", "list", "--state", "all", "--limit", "30", "--json",
                     "number,state,headRefName,headRefOid,mergedAt,reviewDecision,reviews,"
                     "labels,mergeStateStatus,statusCheckRollup"]) or "[]")
by = {p["number"]: p for p in prs}
for n, (then_head, then_tree) in A16["prs"].items():
    p = by.get(n)
    if not p:
        out.append("  >> PR #%d MISSING" % n)
        continue
    tree = sh(["git", "rev-parse", "%s^{tree}" % p["headRefOid"]]).strip()
    out.append("")
    out.append("  PR #%d  %s" % (n, p["headRefName"]))
    out.append("    %-11s head  %s" % (f(p["headRefOid"], then_head), p["headRefOid"]))
    out.append("    %-11s tree  %s" % (f(tree[:12], then_tree), tree))
    out.append("                state=%s merged=%s mergeState=%s reviewDecision=%r reviews=%d labels=%s"
               % (p["state"], p["mergedAt"], p.get("mergeStateStatus"),
                  p.get("reviewDecision"), len(p.get("reviews") or []),
                  [l["name"] for l in p.get("labels", [])]))
    roll = p.get("statusCheckRollup") or []
    bad = [c for c in roll if (c.get("conclusion") or c.get("state")) not in ("SUCCESS", "NEUTRAL", "SKIPPED", None)]
    out.append("                checks: %d total, %d not-success" % (len(roll), len(bad)))
    for c in bad[:6]:
        out.append("                  %-44s %s" % (c.get("name") or c.get("context"),
                                                   c.get("conclusion") or c.get("state")))

t = {n: sh(["git", "rev-parse", "%s^{tree}" % v[0]]).strip() for n, v in A16["prs"].items()}
out.append("")
out.append("  tree identity: #7==#8 ? %s    #6==#7 ? %s" % (t[7] == t[8], t[6] == t[7]))

out.append("")
out.append("=" * 96)
out.append("D. PATCH MANIFEST -- recomputed, nothing trusted")
out.append("=" * 96)
for path, then_pre in A16["patch_pre"].items():
    pre = sh(["git", "rev-parse", "%s:%s" % (A16["prs"][6][0], path)]).strip()
    wt = sh(["git", "hash-object", path]).strip()
    out.append("  %s" % path)
    out.append("    %-11s PR#6 pre-image %s" % (f(pre[:8], then_pre), pre))
    out.append("    %-11s worktree blob  %s" % (f(wt[:8], A16["worktree"][path]), wt))

out.append("")
for cand, then in (("staging/p11b15_proposed_fixed.patch", A16["patch_fixed_bytes"]),
                   ("staging/p11b15_proposed.patch", A16["patch_orig_bytes"])):
    p = Path(cand)
    if p.exists():
        b = p.read_bytes()
        out.append("  %-11s %-44s %d bytes  sha256=%s"
                   % (f(len(b), then), cand, len(b), hashlib.sha256(b).hexdigest()[:16]))
    else:
        out.append("  >> ABSENT   %s" % cand)

out.append("")
out.append("=" * 96)
out.append("E. REPORT DESTINATIONS")
out.append("=" * 96)
for f_ in sorted(Path("staging").glob("PHASE11B_A17*")):
    out.append("  PRESENT %s  %d bytes" % (f_.name, f_.stat().st_size))
else:
    pass
if not list(Path("staging").glob("PHASE11B_A17*")):
    out.append("  no PHASE11B_A17* report exists yet")
out.append("  A16 handoff present: %s"
           % Path("staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md").exists())
out.append("  surviving evidence: p11b14_* = %d files, p11b15_* = %d files"
           % (len(list(Path("staging").glob("p11b14_*"))),
              len(list(Path("staging").glob("p11b15_*")))))

text = "\n".join(out)
open("staging/p11b17_rebaseline.txt", "w", encoding="utf-8").write(text)
print("written %d lines" % len(out))
