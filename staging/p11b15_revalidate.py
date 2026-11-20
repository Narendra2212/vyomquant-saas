"""Phase 11B-A15 step 1: revalidate everything that could have moved since A14. Read-only."""
import hashlib
import json
import subprocess

REPO = "Narendra2212/vyomquant-saas"

A14 = {
    "head": "074b772ab16cece62cbfca0f2b78497be2ae56d5",
    "index_raw": "3dbf1cb07e58b1e9ffb1a70154f9c008ad539188f45ab2cbe2b367fc1caf5676",
    "index_content": "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d",
    "staged": 22,
    "prs": {
        6: "d07014d36d32c63db44f8fb9659933c0e84e82f2",
        7: "49e70da23c2858ffc9a6f34d5db4521401477c82",
        8: "a624ce89b16005b0c73197005d6d0672737dad65",
    },
    "blobs": {
        "tests/test_webhook_duplicate_delivery_real_redis.py": "3ffbccde",
        "tests/test_quota01_metered_reservation_is_hard.py": "7baff0b6",
        "tests/test_quota01_exchange_slot_is_atomic.py": "2d7ccbbb",
        "tests/real_redis_gate.py": "d40a1bb7",
        ".github/workflows/01-pr-check.yml": "91d55a37",
    },
}

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def cmp(label, now, then):
    flag = "SAME" if now == then else ">> CHANGED"
    out.append("  %-10s %-46s now=%s" % (flag, label, now))
    if now != then:
        out.append("  %-10s %-46s A14=%s" % ("", "", then))


out.append("=" * 92)
out.append("1. GIT STATE vs A14")
out.append("=" * 92)
cmp("HEAD", sh(["git", "rev-parse", "HEAD"]).strip(), A14["head"])
cmp("origin/main", sh(["git", "rev-parse", "origin/main"]).strip(), A14["head"])
out.append("  branch: %s" % sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())

raw = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()
cmp("index raw checksum (stat-sensitive)", raw, A14["index_raw"])

stage_lines = sorted(sh(["git", "ls-files", "--stage"]).splitlines())
h = hashlib.sha256()
for l in stage_lines:
    h.update(l.encode("utf-8"))
cmp("index CONTENT digest", h.hexdigest(), A14["index_content"])
out.append("  tracked index entries: %d" % len(stage_lines))

staged = [l for l in sh(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
cmp("staged path count", str(len(staged)), str(A14["staged"]))

unmerged = [l for l in sh(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]
out.append("  unmerged (conflict) entries: %d  %s"
           % (len(unmerged), "OK" if not unmerged else ">> CONFLICTS PRESENT"))

out.append("")
out.append("  staged paths + HEAD->index numstat:")
for line in sh(["git", "diff", "--cached", "--numstat"]).splitlines():
    if line.strip():
        out.append("    " + line)

out.append("")
out.append("=" * 92)
out.append("2. D5' FILE BLOB IDENTITIES vs A14")
out.append("=" * 92)
for path, then in A14["blobs"].items():
    now = sh(["git", "hash-object", path]).strip()[:8]
    cmp(path, now, then)

out.append("")
out.append("=" * 92)
out.append("3. LIVE PR STATE vs A14")
out.append("=" * 92)
prs = json.loads(sh(["gh", "pr", "list", "--state", "all", "--limit", "30", "--json",
                     "number,state,headRefName,headRefOid,baseRefName,mergedAt,isDraft,"
                     "reviewDecision,labels"]) or "[]")
by_num = {p["number"]: p for p in prs}
for n, then in A14["prs"].items():
    p = by_num.get(n)
    if not p:
        out.append("  >> MISSING   PR #%d absent from the listing" % n)
        continue
    cmp("PR #%d head" % n, p["headRefOid"], then)
    out.append("    state=%s draft=%s merged=%s base=%s reviewDecision=%s labels=%s"
               % (p["state"], p["isDraft"], p["mergedAt"], p["baseRefName"],
                  p.get("reviewDecision"), [l["name"] for l in p.get("labels", [])]))

out.append("")
out.append("  remote heads now:")
for line in sh(["git", "ls-remote", "--heads", "origin"]).splitlines():
    if "d5" in line or line.endswith("refs/heads/main"):
        out.append("    " + line)

text = "\n".join(out)
open("staging/p11b15_revalidate.txt", "w", encoding="utf-8").write(text)
print(text)
