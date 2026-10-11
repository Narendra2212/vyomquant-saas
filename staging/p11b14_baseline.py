"""Phase 11B-A14 Phase 0: live PR + branch + CI state. Read-only."""
import json
import subprocess

REPO = "Narendra2212/vyomquant-saas"
out = []


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace"), r.returncode


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


out.append("=" * 84)
out.append("LIVE PR STATE (re-queried; historical SHAs not trusted)")
out.append("=" * 84)
txt, rc = gh(["pr", "list", "--state", "open", "--limit", "30", "--json",
              "number,state,title,headRefName,headRefOid,baseRefName,mergedAt,isDraft,updatedAt"])
prs = json.loads(txt or "[]")
d5 = [p for p in prs if "d5" in p["headRefName"]]
for p in sorted(d5, key=lambda x: x["number"]):
    out.append("")
    out.append("  PR #%d  %s  draft=%s" % (p["number"], p["state"], p["isDraft"]))
    out.append("    head   : %s  %s" % (p["headRefName"], p["headRefOid"]))
    out.append("    base   : %s" % p["baseRefName"])
    out.append("    merged : %s" % p["mergedAt"])
    out.append("    updated: %s" % p["updatedAt"])
    out.append("    title  : %s" % p["title"])

out.append("")
out.append("=" * 84)
out.append("REMOTE BRANCH HEADS (authoritative, now)")
out.append("=" * 84)
for line in sh(["git", "ls-remote", "--heads", "origin"]).splitlines():
    if "d5" in line or line.endswith("refs/heads/main"):
        out.append("  " + line)

out.append("")
out.append("=" * 84)
out.append("LOCAL REFS FOR THE SAME BRANCHES")
out.append("=" * 84)
for ref in ["refs/heads/ci/d5-prime-real-redis",
            "refs/remotes/origin/ci/d5-prime-real-redis",
            "refs/remotes/origin/ci/d5-real-redis-quota-evidence",
            "refs/remotes/origin/ci/d5-real-redis-switch"]:
    v = sh(["git", "rev-parse", "--verify", "-q", ref]).strip()
    out.append("  %-56s %s" % (ref, v or "(absent locally)"))

out.append("")
out.append("=" * 84)
out.append("LATEST CI RUN PER D5 BRANCH")
out.append("=" * 84)
txt, _ = gh(["api", "repos/%s/actions/runs?per_page=60" % REPO, "--jq",
             "[.workflow_runs[] | {id, head_branch, head_sha, status, conclusion, name, created_at}]"])
runs = json.loads(txt or "[]")
seen = set()
for r in runs:
    b = r.get("head_branch") or ""
    if "d5" not in b or r["name"] != "01 PR Check":
        continue
    key = (b, r["head_sha"])
    if key in seen:
        continue
    seen.add(key)
    out.append("  run %s  %-9s %-34s %s  %s"
               % (r["id"], r["conclusion"], b, r["head_sha"][:10], r["created_at"]))

text = "\n".join(out)
open("staging/p11b14_baseline.txt", "w", encoding="utf-8").write(text)
print(text)
