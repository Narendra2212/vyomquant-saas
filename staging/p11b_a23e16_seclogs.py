"""A23E16 -- extract ONLY the security-gate verdict lines from CI logs.

Deliberately narrow: matches severity/summary/error markers and truncates every line, so no
credential, DSN, token or environment dump can be reproduced into the report.
"""
import re
import subprocess
from pathlib import Path

RUNS = [(38129699454, "05 Security (push)"), (38130273012, "03 Deploy")]
KEEP = re.compile(
    r"(Severity:\s*(High|Medium|Critical)|"
    r"^\s*Total issues|issues? \(by severity\)|"
    r"CRITICAL:|HIGH:|"
    r"Test results|No issues identified|"
    r"bandit|Trivy|misconfigurat|"
    r"security scan|conclusion=|"
    r"Error:|##\[error\])", re.I)
REDACT = re.compile(
    r"(postgres(?:ql)?://\S+|[A-Za-z0-9_\-]{32,}|AKIA[0-9A-Z]{16}|"
    r"(?:secret|token|password|passwd|api[_-]?key)\s*[:=]\s*\S+)", re.I)

out = []
for rid, label in RUNS:
    out.append("=" * 96)
    out.append(f"{label}  (run {rid}) -- filtered verdict lines only")
    out.append("=" * 96)
    r = subprocess.run(["gh", "run", "view", str(rid), "--log-failed"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    body = r.stdout or ""
    if not body.strip():
        r = subprocess.run(["gh", "run", "view", str(rid), "--log"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        body = r.stdout or ""
    lines = body.splitlines()
    out.append(f"  log lines retrieved : {len(lines)}")
    kept = 0
    seen = set()
    for l in lines:
        if not KEEP.search(l):
            continue
        clean = REDACT.sub("<redacted>", l.strip())
        clean = clean[-200:] if len(clean) > 200 else clean
        if clean in seen:
            continue
        seen.add(clean)
        kept += 1
        if kept <= 60:
            out.append(f"    {clean}")
    out.append(f"  matched lines (unique) : {kept}")
    out.append("")

Path("staging/p11b_a23e16_seclogs.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("written")
