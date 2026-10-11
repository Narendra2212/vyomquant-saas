"""Phase 11B-A18 preflight. Read-only; writes NO objects into the shared ODB.

Tree/apply verification runs with GIT_OBJECT_DIRECTORY pointed at a disposable directory
and the shared store attached read-only via GIT_ALTERNATE_OBJECT_DIRECTORIES.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

PR6_EXPECTED = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
BRANCH = "ci/d5-real-redis-quota-evidence"
WF = ".github/workflows/01-pr-check.yml"
WEBHOOK = "tests/test_webhook_duplicate_delivery_real_redis.py"
METERED = "tests/test_quota01_metered_reservation_is_hard.py"
SLOT = "tests/test_quota01_exchange_slot_is_atomic.py"
PATHS = [WF, WEBHOOK, METERED]

PRIV_IDX = os.path.abspath("staging/p11b18_verify.index")
PRIV_OBJ = os.path.abspath("staging/p11b18_objdir")

A17 = {"index_content": "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d",
       "staged": 22,
       "head": "074b772ab16cece62cbfca0f2b78497be2ae56d5",
       "patch_fixed_sha": "af3848ef676e6cba",
       "pre": {WF: "57e2cee8", WEBHOOK: "105b6567", METERED: "f48103ac"}}

out = []


def sh(args, stdin=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, input=stdin, env=e)


def t(args, env=None):
    return sh(args, env=env).stdout.decode("utf-8", errors="replace")


def f(now, then):
    return "SAME" if str(now) == str(then) else ">> DIFFERS"


def content_digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


# ============================================================ 1. artifacts
out.append("=" * 98)
out.append("1. REQUIRED ARTIFACTS AND REPORTS")
out.append("=" * 98)
required = [
    "staging/PHASE11B_A17_APPROVAL_RECONCILIATION__claude_session.md",
    "staging/PHASE11B_A17_D5_READINESS__kiro_session.md",
    "staging/p11b15_proposed_fixed.patch",
    "staging/p11b15_proposed.patch",
    "staging/p11b14_preexisting.txt",
    "staging/p11b14_blocking_lane.txt",
    "staging/p11b14_index_digest.txt",
    "staging/p11b14_pr6_unit.log",
    "staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md",
]
missing = []
for r in required:
    p = Path(r)
    if p.exists():
        b = p.read_bytes()
        out.append("  PRESENT  %-62s %9d B  sha256=%s" % (r, len(b), hashlib.sha256(b).hexdigest()[:16]))
    else:
        out.append("  >> MISSING %s" % r)
        missing.append(r)
out.append("")
out.append("  other D5' reports in staging:")
for p in sorted(Path("staging").glob("PHASE11B_A1*.md")):
    out.append("    %-56s %7d B" % (p.name, p.stat().st_size))
out.append("")
out.append("  destination for this phase:")
for d in ["staging/PHASE11B_A18_D5_COMMIT_PUSH_REPORT.md"]:
    out.append("    %-56s %s" % (d, "PRESENT -- must not overwrite" if Path(d).exists() else "ABSENT -- safe"))

# ============================================================ 2. git state
out.append("")
out.append("=" * 98)
out.append("2. LOCAL GIT STATE")
out.append("=" * 98)
head = t(["git", "rev-parse", "HEAD"]).strip()
out.append("  %-11s HEAD                  %s" % (f(head, A17["head"]), head))
out.append("  %-11s origin/main           %s" % (f(t(['git', 'rev-parse', 'origin/main']).strip(), A17["head"]),
                                                 t(["git", "rev-parse", "origin/main"]).strip()))
out.append("              branch (checked out)  %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
con = content_digest()
out.append("  %-11s index CONTENT digest  %s" % (f(con, A17["index_content"]), con))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("  %-11s staged paths          %d" % (f(len(staged), A17["staged"]), len(staged)))
unmerged = [l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]
out.append("  %-11s unmerged entries      %d" % ("SAME" if not unmerged else ">> CONFLICTS", len(unmerged)))

out.append("")
out.append("  22 staged paths with INDEX blob identity:")
for line in t(["git", "ls-files", "--stage"]).splitlines():
    m = re.match(r"(\d+) ([0-9a-f]+) (\d)\t(.*)", line)
    if m and m.group(4) in staged:
        out.append("    %s %s  %s" % (m.group(2)[:12], m.group(1), m.group(4)))

out.append("")
out.append("  local worktrees / branch refs:")
for l in t(["git", "worktree", "list"]).splitlines():
    out.append("    worktree: %s" % l)
for ref in ["refs/heads/" + BRANCH, "refs/remotes/origin/" + BRANCH]:
    v = t(["git", "rev-parse", "--verify", "-q", ref]).strip()
    out.append("    %-62s %s" % (ref, v or "(absent locally)"))

# ============================================================ 3. remote state
out.append("")
out.append("=" * 98)
out.append("3. FRESH REMOTE EVIDENCE")
out.append("=" * 98)
out.append("  git ls-remote --heads origin (D5 branches + main):")
remote_heads = {}
for line in t(["git", "ls-remote", "--heads", "origin"]).splitlines():
    sha, ref = line.split("\t")
    remote_heads[ref] = sha
    if "d5" in ref or ref.endswith("/main"):
        out.append("    %s  %s" % (sha, ref))
pr6_remote = remote_heads.get("refs/heads/" + BRANCH, "")
out.append("")
out.append("  %-11s PR #6 branch remote head  %s" % (f(pr6_remote, PR6_EXPECTED), pr6_remote))

prs = json.loads(t(["gh", "pr", "list", "--state", "all", "--limit", "30", "--json",
                    "number,state,headRefName,headRefOid,mergedAt,reviewDecision,"
                    "mergeStateStatus,statusCheckRollup"]) or "[]")
by = {p["number"]: p for p in prs}
for n in (6, 7, 8):
    p = by.get(n)
    if not p:
        out.append("  >> PR #%d MISSING" % n)
        continue
    tree = t(["git", "rev-parse", "%s^{tree}" % p["headRefOid"]]).strip()
    roll = p.get("statusCheckRollup") or []
    bad = [c for c in roll if (c.get("conclusion") or c.get("state")) not in
           ("SUCCESS", "NEUTRAL", "SKIPPED", None)]
    out.append("")
    out.append("  PR #%d %-34s head=%s" % (n, p["headRefName"], p["headRefOid"]))
    out.append("        tree=%s state=%s merged=%s mergeState=%s review=%r"
               % (tree[:12], p["state"], p["mergedAt"], p.get("mergeStateStatus"),
                  p.get("reviewDecision")))
    out.append("        checks=%d not-success=%d  %s"
               % (len(roll), len(bad), [c.get("name") for c in bad]))

# ============================================================ 4. patch verify
out.append("")
out.append("=" * 98)
out.append("4. PATCH VERIFICATION AGAINST THE FRESH PR #6 HEAD")
out.append("=" * 98)
patch = Path("staging/p11b15_proposed_fixed.patch").read_bytes()
out.append("  patch sha256 %s  (%s vs A17)  %d bytes"
           % (hashlib.sha256(patch).hexdigest()[:16],
              f(hashlib.sha256(patch).hexdigest()[:16], A17["patch_fixed_sha"]), len(patch)))

paths_in_patch = [m.decode() for m in re.findall(rb"^diff --git a/(\S+)", patch, re.M)]
out.append("  paths in patch (%d): %s" % (len(paths_in_patch), paths_in_patch))
out.append("  %-11s exactly the three approved paths"
           % ("SAME" if paths_in_patch == PATHS else ">> DIFFERS"))

out.append("")
for p in PATHS:
    pre = t(["git", "rev-parse", "%s:%s" % (pr6_remote or PR6_EXPECTED, p)]).strip()
    out.append("  %-11s %-52s pre-image %s" % (f(pre[:8], A17["pre"][p]), p, pre))

# isolated object dir so nothing is written to the shared ODB
if os.path.exists(PRIV_IDX):
    os.remove(PRIV_IDX)
if os.path.isdir(PRIV_OBJ):
    shutil.rmtree(PRIV_OBJ)
os.makedirs(PRIV_OBJ, exist_ok=True)
iso = {"GIT_INDEX_FILE": PRIV_IDX,
       "GIT_OBJECT_DIRECTORY": PRIV_OBJ,
       "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.abspath(".git/objects")}

shared_objs_before = sum(1 for _ in Path(".git/objects").rglob("*") if _.is_file())

sh(["git", "read-tree", pr6_remote or PR6_EXPECTED], env=iso)
r = sh(["git", "apply", "--cached", "--check", "-"], stdin=patch, env=iso)
out.append("")
out.append("  git apply --cached --check (isolated index + isolated ODB): rc=%d  %s"
           % (r.returncode, "APPLIES CLEANLY" if r.returncode == 0 else ">> REJECTED"))
for l in r.stderr.decode(errors="replace").splitlines():
    out.append("    %s" % l)

# dry-run the real apply + write-tree inside isolation to learn the resulting tree
r2 = sh(["git", "apply", "--cached", "-"], stdin=patch, env=iso)
tree_candidate = t(["git", "write-tree"], env=iso).strip()
out.append("  isolated apply rc=%d ; resulting candidate tree = %s" % (r2.returncode, tree_candidate))
names = [l.split("\t")[-1] for l in t(["git", "diff", "--cached", "--name-only",
                                       pr6_remote or PR6_EXPECTED], env=iso).splitlines() if l.strip()]
out.append("  paths changed vs PR #6 tree in the isolated index (%d): %s" % (len(names), names))

shared_objs_after = sum(1 for _ in Path(".git/objects").rglob("*") if _.is_file())
out.append("")
out.append("  shared .git/objects file count before/after: %d / %d  %s"
           % (shared_objs_before, shared_objs_after,
              "UNCHANGED" if shared_objs_before == shared_objs_after else ">> OBJECTS WRITTEN"))

os.remove(PRIV_IDX) if os.path.exists(PRIV_IDX) else None
shutil.rmtree(PRIV_OBJ, ignore_errors=True)
out.append("  isolated index + object dir removed: %s / %s"
           % (not os.path.exists(PRIV_IDX), not os.path.isdir(PRIV_OBJ)))

# ============================================================ 5. intent checks
out.append("")
out.append("=" * 98)
out.append("5. INTENT CHECKS ON THE PATCH")
out.append("=" * 98)
txt = patch.decode("utf-8", errors="replace")
start = txt.index("diff --git a/%s" % WF)
nxt = txt.find("diff --git a/tests", start)
wf_sec = txt[start:nxt if nxt > 0 else len(txt)]
adds = [l for l in wf_sec.splitlines() if l.startswith("+") and not l.startswith("+++")]
dels = [l for l in wf_sec.splitlines() if l.startswith("-") and not l.startswith("---")]
out.append("  workflow: +%d / -%d  hunks=%s" % (len(adds), len(dels),
                                                re.findall(r"^@@[^\n]*", wf_sec, re.M)))
for l in dels + adds:
    out.append("    %s" % l.strip()[:92])
out.append("  all changed lines are AERORA_TEST_REDIS_URL: %s"
           % all("AERORA_TEST_REDIS_URL" in l for l in adds + dels))
out.append("  /15 -> /0 only: %s"
           % (all(l.rstrip().endswith("/15") for l in dels) and
              all(l.rstrip().endswith("/0") for l in adds)))
wf6 = t(["git", "show", "%s:%s" % (pr6_remote or PR6_EXPECTED, WF)])
out.append("  diagnostics job in base: %s ; patch touches it: %s"
           % (bool(re.search(r"^\s{2}d5-redis-diagnostics:", wf6, re.M)),
              any("d5-redis-diagnostics" in l for l in adds + dels)))
out.append("  patch touches provisioning job: %s"
           % any("provisioning-and-isolation" in l for l in adds + dels))

for p, label in ((WEBHOOK, "webhook"), (METERED, "metered")):
    base = t(["git", "show", "%s:%s" % (pr6_remote or PR6_EXPECTED, p)])
    new = Path(p).read_text(encoding="utf-8", errors="replace")
    tb = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", base, re.M)
    tn = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", new, re.M)
    ex_skip = sum(1 for l in new.splitlines() if l.strip().startswith("pytest.skip("))
    ex_fail = sum(1 for l in new.splitlines() if l.strip().startswith("pytest.fail("))
    out.append("")
    out.append("  %s: base=%d new=%d removed=%s added=%s"
               % (label, len(tb), len(tn), [x for x in tb if x not in tn] or "none",
                  [x for x in tn if x not in tb] or "none"))
    out.append("    executable pytest.skip(=%d   executable pytest.fail(=%d   require_real_redis()=%d"
               % (ex_skip, ex_fail, new.count("require_real_redis()")))

out.append("")
out.append("  exchange-slot test blob (must stay unchanged): %s"
           % t(["git", "hash-object", SLOT]).strip())

out.append("")
out.append("=" * 98)
out.append("PREFLIGHT VERDICT")
out.append("=" * 98)
ok = (not missing and head == A17["head"] and con == A17["index_content"]
      and len(staged) == 22 and not unmerged
      and pr6_remote == PR6_EXPECTED and paths_in_patch == PATHS
      and r.returncode == 0 and shared_objs_before == shared_objs_after)
out.append("  ALL PRECONDITIONS PASS: %s" % ok)
out.append("  candidate tree for the commit: %s" % tree_candidate)

open("staging/p11b18_preflight.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines; preconditions pass = %s" % (len(out), ok))
