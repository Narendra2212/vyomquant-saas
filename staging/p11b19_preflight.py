"""A19 Phase 0: read-only preflight + current CI status for both runs."""
import hashlib
import json
import subprocess
from pathlib import Path

COMMIT = "f5b99e57e1f7644bdbb10712e21f85e2ff236920"
PARENT = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
REPO = "Narendra2212/vyomquant-saas"
RUNS = {"38061973739": "01 PR Check", "38061973701": "05 Security"}
BASELINE_RUN = "37346853502"
A18_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"

out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


out.append("=" * 100)
out.append("0A. LOCAL / REMOTE PRESERVATION STATE")
out.append("=" * 100)
out.append("  branch              : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  local main HEAD     : %s" % t(["git", "rev-parse", "HEAD"]).strip())
d = digest()
out.append("  index content digest: %s  %s" % (d, "SAME" if d == A18_DIGEST else ">> CHANGED"))
out.append("  staged paths        : %d" % len([l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]))
out.append("  slot test blob      : %s" % t(["git", "hash-object", "tests/test_quota01_exchange_slot_is_atomic.py"]).strip())

out.append("")
out.append("=" * 100)
out.append("0B. PR #6 REMOTE HEAD")
out.append("=" * 100)
rem = t(["git", "ls-remote", "origin", "refs/heads/ci/d5-real-redis-quota-evidence"]).split()
out.append("  ls-remote : %s" % (rem[0] if rem else "(none)"))
out.append("  expected  : %s  %s" % (COMMIT, "MATCH" if rem and rem[0] == COMMIT else ">> DIFFERS"))
pr = json.loads(t(["gh", "pr", "view", "6", "--json",
                   "number,state,headRefOid,mergedAt,mergeStateStatus"]))
out.append("  gh pr view 6: %s" % json.dumps(pr))
for n in (7, 8):
    out.append("  gh pr view %d: %s" % (n, t(["gh", "pr", "view", str(n), "--json",
                                              "number,state,headRefOid,closed"]).strip()))

out.append("")
out.append("=" * 100)
out.append("0C. CI RUN STATUS")
out.append("=" * 100)
for rid, name in RUNS.items():
    r = json.loads(t(["gh", "api", "repos/%s/actions/runs/%s" % (REPO, rid)]) or "{}")
    out.append("")
    out.append("  run %s  (%s)" % (rid, name))
    out.append("    name=%s  event=%s  attempt=%s" % (r.get("name"), r.get("event"), r.get("run_attempt")))
    out.append("    head_sha=%s  %s" % (r.get("head_sha"), "== our commit" if r.get("head_sha") == COMMIT else ">> OTHER"))
    out.append("    head_branch=%s" % r.get("head_branch"))
    out.append("    status=%s  conclusion=%s" % (r.get("status"), r.get("conclusion")))
    out.append("    created=%s  updated=%s" % (r.get("created_at"), r.get("updated_at")))
    jobs = json.loads(t(["gh", "api", "repos/%s/actions/runs/%s/jobs?per_page=50" % (REPO, rid),
                         "--jq", "[.jobs[] | {id,name,status,conclusion,started_at,completed_at}]"]) or "[]")
    out.append("    jobs (%d):" % len(jobs))
    for j in jobs:
        out.append("      %-13s %-11s %-42s %s -> %s"
                   % (j["id"], j["conclusion"] or j["status"], j["name"][:42],
                      (j["started_at"] or "")[11:19], (j["completed_at"] or "")[11:19]))
    arts = json.loads(t(["gh", "api", "repos/%s/actions/runs/%s/artifacts" % (REPO, rid),
                         "--jq", "{n:.total_count, a:[.artifacts[]|{id,name,size_in_bytes,expired}]}"]) or "{}")
    out.append("    artifacts: %s" % json.dumps(arts))

out.append("")
out.append("=" * 100)
out.append("0D. BASELINE SOURCES STILL VALID?")
out.append("=" * 100)
b = json.loads(t(["gh", "api", "repos/%s/actions/runs/%s" % (REPO, BASELINE_RUN)]) or "{}")
out.append("  baseline run %s: name=%s branch=%s sha=%s conclusion=%s created=%s"
           % (BASELINE_RUN, b.get("name"), b.get("head_branch"), (b.get("head_sha") or "")[:12],
              b.get("conclusion"), b.get("created_at")))
p = Path("staging/p11b14_preexisting.txt")
out.append("  p11b14_preexisting.txt: %s  %d B  sha256=%s"
           % ("PRESENT" if p.exists() else "MISSING", p.stat().st_size if p.exists() else 0,
              hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "-"))
out.append("  baseline sha is an ancestor of main: %s"
           % (subprocess.run(["git", "merge-base", "--is-ancestor", b.get("head_sha", ""), "origin/main"],
                             capture_output=True).returncode == 0 if b.get("head_sha") else "unknown"))

out.append("")
out.append("=" * 100)
out.append("0E. OTHER EXECUTORS / NEWER REPORTS")
out.append("=" * 100)
for l in t(["git", "worktree", "list"]).splitlines():
    out.append("  worktree: %s" % l)
out.append("")
for pat in ("PHASE11B_A19*", "PHASE11B_A18*"):
    hits = sorted(Path("staging").glob(pat))
    out.append("  %-20s %s" % (pat, [h.name for h in hits] or "none"))
out.append("  destination staging/PHASE11B_A19_D5_CI_EVIDENCE_REPORT.md : %s"
           % ("PRESENT -- must not overwrite"
              if Path("staging/PHASE11B_A19_D5_CI_EVIDENCE_REPORT.md").exists() else "ABSENT -- safe"))

open("staging/p11b19_preflight.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
