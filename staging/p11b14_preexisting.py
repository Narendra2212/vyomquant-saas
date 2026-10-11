"""Are the 39 non-D5' failures pre-existing? Check a run that carries no D5' files."""
import collections
import json
import re
import subprocess

REPO = "Narendra2212/vyomquant-saas"


def gh(args):
    return subprocess.run(["gh"] + args, capture_output=True).stdout


out = []
runs = json.loads(gh(["api", f"repos/{REPO}/actions/runs?per_page=80", "--jq",
                      "[.workflow_runs[] | {id, head_branch, head_sha, conclusion, name, created_at}]"]) or b"[]")

cands = [r for r in runs
         if r["name"] == "01 PR Check" and "d5" not in (r["head_branch"] or "")]
out.append("non-D5' '01 PR Check' runs available:")
for r in cands[:10]:
    out.append("  run %s  %-9s %-40s %s  %s"
               % (r["id"], r["conclusion"], r["head_branch"], r["head_sha"][:10], r["created_at"]))

if not cands:
    out.append("  (none)")
else:
    target = cands[0]
    out.append("")
    out.append("Comparing against run %s (%s)" % (target["id"], target["head_branch"]))
    jobs = json.loads(gh(["api", f"repos/{REPO}/actions/runs/{target['id']}/jobs?per_page=50",
                          "--jq", "[.jobs[] | {id, name, conclusion}]"]) or b"[]")
    # Must be the BLOCKING python lane, not "Frontend Build & Unit Tests", which also
    # contains the substring "Unit" and reports 2 passed / 5 deselected.
    unit = next((j for j in jobs if j["name"].startswith("Unit & Integration")), None)
    out.append("  all jobs: %s" % [j["name"] for j in jobs])
    out.append("  selected unit job: %s" % unit)
    if unit:
        raw = gh(["api", "--allow-escape-sequences",
                  f"repos/{REPO}/actions/jobs/{unit['id']}/logs"])
        txt = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw.decode("utf-8", errors="replace"))
        lines = txt.splitlines()
        for l in lines:
            if re.search(r"=+ \d+ failed", l) or re.search(r"=+ \d+ passed", l):
                out.append("  TOTALS: " + l.strip()[-150:])
        ids = set()
        for l in lines:
            if re.search(r"\sFAILED\s*(\[|$)", l):
                m = re.search(r"(tests/\S+?)\s+FAILED", l)
                if m:
                    ids.add(m.group(1))
        c = collections.Counter(i.split("::")[0] for i in ids)
        out.append("")
        out.append("  distinct FAILED node ids: %d" % len(ids))
        for f, n in c.most_common():
            out.append("    %-4d %s" % (n, f))

        PR6 = {
            "tests/test_no_credential_in_api_response_body.py": 13,
            "tests/test_validation_sweep.py": 9,
            "tests/test_task_6_1_backtest_execute.py": 7,
            "tests/test_strategy_status_publish.py": 4,
            "tests/test_task_33_2_rate_limit_keys.py": 1,
            "tests/property/test_tenant_isolation_matrix.py": 1,
            "tests/test_task_8_4_runtime_readiness_gate.py": 1,
            "tests/test_ml_deployment_guards.py": 1,
        }
        out.append("")
        out.append("  %-56s %-8s %s" % ("file", "on PR6", "on this non-D5' run"))
        for f, n in PR6.items():
            out.append("  %-56s %-8d %s" % (f, n, c.get(f, 0)))

text = "\n".join(out)
open("staging/p11b14_preexisting.txt", "w", encoding="utf-8").write(text)
print(text)
