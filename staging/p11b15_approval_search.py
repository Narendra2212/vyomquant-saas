"""Has any A14 approval been explicitly recorded? Read-only search of remote + repo."""
import json
import re
import subprocess
from pathlib import Path

REPO = "Narendra2212/vyomquant-saas"
out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


out.append("=" * 90)
out.append("A. PR REVIEWS, COMMENTS, LABELS, ASSIGNEES  (#6, #7, #8)")
out.append("=" * 90)
for n in (6, 7, 8):
    raw = sh(["gh", "pr", "view", str(n), "--json",
              "number,reviewDecision,reviews,comments,labels,assignees,body,title"])
    try:
        p = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        out.append("  PR #%d: could not parse (%s)" % (n, exc))
        continue
    out.append("")
    out.append("  PR #%d  %s" % (n, p.get("title", "")[:70]))
    out.append("    reviewDecision : %r" % p.get("reviewDecision"))
    out.append("    reviews        : %d" % len(p.get("reviews") or []))
    for r in (p.get("reviews") or []):
        out.append("      - %s by %s: %s"
                   % (r.get("state"), (r.get("author") or {}).get("login"),
                      (r.get("body") or "")[:90]))
    out.append("    comments       : %d" % len(p.get("comments") or []))
    for c in (p.get("comments") or []):
        out.append("      - %s: %s"
                   % ((c.get("author") or {}).get("login"), (c.get("body") or "")[:110]))
    out.append("    labels         : %s" % [l["name"] for l in (p.get("labels") or [])])
    out.append("    assignees      : %s"
               % [a.get("login") for a in (p.get("assignees") or [])])

out.append("")
out.append("=" * 90)
out.append("B. REPO-SIDE APPROVAL RECORDS")
out.append("=" * 90)

PAT = re.compile(
    r"(approv\w*|authoris\w*|authoriz\w*|sign[- ]?off|canonical)\b", re.I)
HITS = re.compile(
    r"(canonical\s+PR|PR\s*#\s*[678]\b.{0,60}(approved|authoris|authoriz)"
    r"|(approved|authoris\w*|authoriz\w*).{0,60}PR\s*#\s*[678]\b)", re.I)

cands = sorted(Path("staging").glob("*.md")) + sorted(Path("staging").glob("*.json"))
out.append("  scanned %d staging documents for an explicit approval of a canonical PR" % len(cands))
found = []
for f in cands:
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for m in HITS.finditer(text):
        line_no = text[:m.start()].count("\n") + 1
        line = text.splitlines()[line_no - 1].strip()
        found.append((f.name, line_no, line[:140]))

if not found:
    out.append("  >> NO explicit canonical-PR approval statement found in staging/")
else:
    for name, ln, line in found[:40]:
        out.append("    %s:%d  %s" % (name, ln, line))

# Dedicated approval files?
out.append("")
approval_files = [p for p in Path(".").rglob("*")
                  if p.is_file() and re.search(r"(APPROV|AUTHORIS|AUTHORIZ|SIGNOFF|SIGN_OFF)",
                                               p.name, re.I)
                  and ".git" not in p.parts]
out.append("  files whose NAME records an approval: %d" % len(approval_files))
for p in approval_files[:20]:
    out.append("    %s" % p)

text = "\n".join(out)
open("staging/p11b15_approval_search.txt", "w", encoding="utf-8").write(text)
print(text)
