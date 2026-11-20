"""A23E16 Phase 3 -- CI evidence for the launch commit. READ-ONLY: `gh run view` only.

No workflow is dispatched, re-run, or edited. No log containing a DSN or secret is printed;
only job names, conclusions and step conclusions are extracted.
"""
import json
import subprocess
from pathlib import Path

HEAD = "ecd23a9e934ec58a7b1f0e5db638da8e865e8187"
RUNS = [
    (38129699454, "05 Security", "push"),
    (38129699471, "02 Build", "push"),
    (38130273012, "03 Deploy", "workflow_run"),
    (38132697285, "05 Security", "schedule"),
    (38129699497, "06 Frontend Deploy", "push"),
    (38129699516, "07 Desktop Build", "push"),
]
out = []


def sh(args):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


out.append("=" * 96)
out.append(f"CI EVIDENCE FOR THE LAUNCH COMMIT {HEAD[:12]}")
out.append("=" * 96)

# Which workflows ran at all for this SHA?
r = sh(["gh", "run", "list", "--limit", "60", "--json",
        "databaseId,headSha,name,event,status,conclusion"])
try:
    allruns = json.loads(r.stdout)
except Exception:
    allruns = []
mine = [x for x in allruns if x.get("headSha") == HEAD]
names = sorted({x["name"] for x in mine})
out.append(f"  runs found for this SHA : {len(mine)}")
out.append(f"  workflows that ran      : {names}")

wf_dir = Path(".github/workflows")
defined = sorted(p.name for p in wf_dir.glob("*.yml"))
out.append("")
out.append("  workflow files defined in the repo, and whether each ran on this SHA:")
for f in defined:
    text = (wf_dir / f).read_text(encoding="utf-8", errors="replace")
    first = text.split("name:", 1)[1].splitlines()[0].strip() if "name:" in text else f
    ran = any(x["name"] == first for x in mine)
    # crude trigger extraction: the `on:` block up to the first top-level key
    trig = []
    for key in ("push:", "pull_request:", "schedule:", "workflow_dispatch:",
                "workflow_run:", "pull_request_target:"):
        if f"\n  {key}" in text:
            trig.append(key.rstrip(":"))
    out.append(f"    {'RAN    ' if ran else 'NO RUN '} {f:<26} triggers={trig}")

out.append("")
out.append("=" * 96)
out.append("PER-RUN JOB OUTCOMES")
out.append("=" * 96)
for rid, label, event in RUNS:
    rr = sh(["gh", "run", "view", str(rid), "--json", "jobs,conclusion,status"])
    out.append("")
    out.append(f"  --- {label}  (id {rid}, event {event}) ---")
    try:
        d = json.loads(rr.stdout)
    except Exception:
        out.append(f"      <could not parse: {rr.stderr.strip()[:160]}>")
        continue
    out.append(f"      run conclusion : {d.get('conclusion')}")
    for j in d.get("jobs", []):
        out.append(f"      job  {str(j.get('conclusion')):<12} {j.get('name')}")
        for s in j.get("steps", []):
            if s.get("conclusion") in ("failure", "cancelled"):
                out.append(f"           step {s.get('conclusion'):<10} {s.get('name')}")

Path("staging/p11b_a23e16_ci.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("written")
