"""Summarise the local reproduction of CI's bandit gate command. READ-ONLY."""
import json
import subprocess
from pathlib import Path

rep = json.loads(Path("staging/p11b_a23e16_bandit.json").read_text(encoding="utf-8"))
findings = rep.get("results", [])
out = []
out.append("=" * 96)
out.append("LOCAL REPRODUCTION OF CI's GATE COMMAND")
out.append("  bandit -r backend_app -ll -f json   (exit 1 == gate fails)")
out.append("=" * 96)
m = rep.get("metrics", {}).get("_totals", {})
out.append(f"  files scanned      : {len(rep.get('metrics', {})) - 1}")
out.append(f"  LOC               : {m.get('loc')}")
out.append(f"  SEVERITY.HIGH     : {m.get('SEVERITY.HIGH')}")
out.append(f"  SEVERITY.MEDIUM   : {m.get('SEVERITY.MEDIUM')}")
out.append(f"  SEVERITY.LOW      : {m.get('SEVERITY.LOW')}")
out.append(f"  reported findings (MEDIUM+ at -ll) : {len(findings)}")
out.append("")

by_test = {}
for f in findings:
    by_test.setdefault((f["test_id"], f["issue_severity"], f["issue_confidence"]), []).append(f)

out.append("  grouped by rule:")
for (tid, sev, conf), fs in sorted(by_test.items(), key=lambda kv: -len(kv[1])):
    out.append(f"    {tid}  {sev}/{conf}  x{len(fs)}  -- {fs[0]['issue_text'][:88]}")

out.append("")
out.append("  every finding, with whether its file is NEW in the launch commit:")
head_files = set(subprocess.run(
    ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", "--diff-filter=A", "HEAD"],
    capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split())
for f in sorted(findings, key=lambda x: (x["filename"], x["line_number"])):
    rel = f["filename"].replace("\\", "/")
    isnew = rel in head_files
    out.append(f"    {f['test_id']}  {f['issue_severity']:<6} {rel}:{f['line_number']}"
               f"   new_in_launch_commit={isnew}")

out.append("")
out.append("  classification inputs:")
out.append(f"    findings in files ADDED by the launch commit : "
           f"{sum(1 for f in findings if f['filename'].replace(chr(92),'/') in head_files)}")
out.append(f"    findings in pre-existing files               : "
           f"{sum(1 for f in findings if f['filename'].replace(chr(92),'/') not in head_files)}")

Path("staging/p11b_a23e16_bandit_sum.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
