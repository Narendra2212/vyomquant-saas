"""A18 §7: independent post-action verification, from host-side evidence where possible."""
import hashlib
import json
import re
import subprocess

COMMIT = "f5b99e57e1f7644bdbb10712e21f85e2ff236920"
PARENT = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
REPO = "Narendra2212/vyomquant-saas"
SLOT = "tests/test_quota01_exchange_slot_is_atomic.py"
WF = ".github/workflows/01-pr-check.yml"
WEBHOOK = "tests/test_webhook_duplicate_delivery_real_redis.py"
METERED = "tests/test_quota01_metered_reservation_is_hard.py"
A17_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"

out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


out.append("=" * 98)
out.append("1. HOST-SIDE: PR #6 POINTS AT THE INTENDED COMMIT, AND ITS FILE LIST")
out.append("=" * 98)
pr = json.loads(t(["gh", "pr", "view", "6", "--json",
                   "number,state,headRefOid,headRefName,mergedAt,mergeStateStatus,"
                   "files,additions,deletions,changedFiles"]))
out.append("  PR #6 head = %s  %s" % (pr["headRefOid"], "== our commit" if pr["headRefOid"] == COMMIT else ">> MISMATCH"))
out.append("  state=%s merged=%s mergeState=%s" % (pr["state"], pr["mergedAt"], pr["mergeStateStatus"]))
out.append("  PR totals vs base: changedFiles=%d +%d/-%d"
           % (pr["changedFiles"], pr["additions"], pr["deletions"]))
out.append("  PR file list (whole branch vs main):")
for fl in pr["files"]:
    out.append("    %-56s +%-5d -%-5d" % (fl["path"], fl["additions"], fl["deletions"]))

out.append("")
out.append("=" * 98)
out.append("2. HOST-SIDE: WHAT THIS COMMIT ALONE CHANGED")
out.append("=" * 98)
cmp_api = json.loads(t(["gh", "api", "repos/%s/compare/%s...%s" % (REPO, PARENT, COMMIT)]))
out.append("  compare %s...%s" % (PARENT[:12], COMMIT[:12]))
out.append("  status=%s ahead_by=%d behind_by=%d total_commits=%d"
           % (cmp_api["status"], cmp_api["ahead_by"], cmp_api["behind_by"],
              cmp_api["total_commits"]))
out.append("  files (%d):" % len(cmp_api["files"]))
for fl in cmp_api["files"]:
    out.append("    %-56s %-9s +%-5d -%-5d" % (fl["filename"], fl["status"],
                                               fl["additions"], fl["deletions"]))
names = sorted(fl["filename"] for fl in cmp_api["files"])
out.append("  exactly the three approved paths: %s"
           % (names == sorted([WF, WEBHOOK, METERED])))

wf_file = next((f for f in cmp_api["files"] if f["filename"] == WF), None)
if wf_file:
    out.append("")
    out.append("  workflow patch as the host records it (+%d/-%d):"
               % (wf_file["additions"], wf_file["deletions"]))
    for l in (wf_file.get("patch") or "").splitlines():
        if l.startswith(("@@", "+", "-")) and not l.startswith(("+++", "---")):
            out.append("    %s" % l.strip()[:94])
    out.append("  workflow is exactly +2/-2: %s"
               % (wf_file["additions"] == 2 and wf_file["deletions"] == 2))

out.append("")
out.append("=" * 98)
out.append("3. CONTENT OF THE PUSHED COMMIT")
out.append("=" * 98)
slot_new = t(["git", "rev-parse", "%s:%s" % (COMMIT, SLOT)]).strip()
slot_old = t(["git", "rev-parse", "%s:%s" % (PARENT, SLOT)]).strip()
out.append("  exchange-slot test blob  parent=%s  commit=%s  BYTE-UNCHANGED=%s"
           % (slot_old[:12], slot_new[:12], slot_old == slot_new))

for path, label, base_n in ((WEBHOOK, "webhook", 6), (METERED, "metered", 7)):
    new = t(["git", "show", "%s:%s" % (COMMIT, path)])
    tests = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", new, re.M)
    ex_skip = sum(1 for l in new.splitlines() if l.strip().startswith("pytest.skip("))
    ex_fail = sum(1 for l in new.splitlines() if l.strip().startswith("pytest.fail("))
    base = t(["git", "show", "%s:%s" % (PARENT, path)])
    base_tests = re.findall(r"^\s*(?:async\s+)?def (test_\w+)", base, re.M)
    out.append("")
    out.append("  %s in the pushed commit: %d tests (base had %d)" % (label, len(tests), len(base_tests)))
    out.append("    all base tests preserved: %s" % all(b in tests for b in base_tests))
    out.append("    executable pytest.skip( = %d   executable pytest.fail( = %d" % (ex_skip, ex_fail))
    if label == "metered":
        for want in ("test_production_reserve_quota_admits_exactly_the_limit_against_real_redis",
                     "test_production_zero_allowance_writes_no_meter_against_real_redis"):
            out.append("    present: %-74s %s" % (want, want in tests))

out.append("")
out.append("=" * 98)
out.append("4. CI OBSERVED, NOT TRIGGERED")
out.append("=" * 98)
runs = json.loads(t(["gh", "api", "repos/%s/actions/runs?per_page=20" % REPO, "--jq",
                     "[.workflow_runs[] | {id, name, head_sha, head_branch, event, status, "
                     "conclusion, created_at, run_attempt}]"]) or "[]")
mine = [r for r in runs if r["head_sha"] == COMMIT]
out.append("  runs whose head_sha == our commit: %d" % len(mine))
for r in mine:
    out.append("    run %s  %-18s event=%-14s status=%-11s conclusion=%s attempt=%s"
               % (r["id"], r["name"][:18], r["event"], r["status"], r["conclusion"], r["run_attempt"]))
out.append("")
out.append("  These were started automatically by the Git host's configured triggers on a")
out.append("  normal push. No workflow_dispatch, no re-run, no `gh run` invocation was issued.")

out.append("")
out.append("=" * 98)
out.append("5. PRESERVATION INVARIANTS")
out.append("=" * 98)
d = digest()
out.append("  shared index CONTENT digest : %s  %s" % (d, "SAME" if d == A17_DIGEST else ">> CHANGED"))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("  staged paths                : %d" % len(staged))
out.append("  unmerged entries            : %d" % len([l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]))
out.append("  checked-out branch          : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  local main HEAD             : %s" % t(["git", "rev-parse", "HEAD"]).strip())
out.append("  remote main                 : %s" % t(["git", "ls-remote", "origin", "refs/heads/main"]).split()[0])
out.append("")
out.append("  worktree status of protected paths:")
for p in (".gitignore", "backend_app/core/subscription_dependencies.py",
          "backend_app/routers/exchange.py", SLOT):
    st = t(["git", "status", "--porcelain", "--", p]).rstrip()
    num = t(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    out.append("    %-52s %-5s %s" % (p, st[:2] if st else "  ",
                                      ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"))
out.append("")
out.append("  PR #7 / #8 state:")
for n in (7, 8):
    out.append("    %s" % t(["gh", "pr", "view", str(n), "--json",
                             "number,state,headRefOid,mergedAt,closed"]).strip())

open("staging/p11b18_verify.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
