"""A24 step 2: extract headline / status / approval lines from the authoritative reports."""
import re
from pathlib import Path

REPORTS = [
    ("REDIS lifecycle (newest)", "staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md"),
    ("REDIS consolidation", "staging/PHASE11B_A23C_REDIS_RECOVERY_CONSOLIDATION.md"),
    ("REDIS coverage", "staging/PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md"),
    ("REDIS loop identity", "staging/PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md"),
    ("QUOTA-01 (newest)", "staging/PHASE11B_A20Q_QUOTA_REFUSAL_AND_LOOP_LIFECYCLE.md"),
    ("TENANT-01 (newest)", "staging/PHASE11B_TENANT01_U4_U6_INVESTIGATION_REPORT.md"),
    ("TENANT-01 coordination", "staging/PHASE11B_TENANT01_FAST_COORDINATION_REPORT.md"),
    ("CD-8 gate", "staging/PHASE11B_U9_CD8_AUTHORIZATION_GATE_AND_SCHEMA_FINDINGS.md"),
    ("CD-8 recovery", "staging/PHASE11B_U7_CD8_CRASH_RECOVERY_READINESS.md"),
    ("W1C-02 classifier (newest)", "staging/PHASE11B_W1D10_CLASSIFIER_IMPLEMENTATION_READINESS.md"),
    ("W1C-02 remediation", "staging/PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md"),
    ("W1D static drift guards", "staging/PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md"),
    ("Watchdog", "staging/PHASE11B_D5_WATCHDOG_REPAIR_REPORT.md"),
    ("DUP-02 recovery", "staging/PHASE11B_D4_DUP02_RECOVERY_READINESS.md"),
    ("Launch gate register", "staging/PHASE11A_LAUNCH_GATE_REGISTER.md"),
    ("Launch blocker register", "staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md"),
    ("Readiness scorecard", "staging/PHASE11A_READINESS_SCORECARD.json"),
]

STATUS = re.compile(
    r"(STATUS:|VERDICT|HEADLINE|NOT GRANTED|GRANTED|REJECTED|UNRESOLVED|BLOCKED"
    r"|no implementation|NOT PERFORMED|NOT IMPLEMENTED|IMPLEMENTED|awaiting"
    r"|OPEN\b|CLOSED\b|UNPROVEN|UNVERIFIED|NOT CERTIFIED|READY)", re.I)
APPROVAL = re.compile(r"(approv|authoris|authoriz|grant)", re.I)

out = []
for label, path in REPORTS:
    p = Path(path)
    out.append("=" * 104)
    if not p.exists():
        out.append("%s  -> ABSENT: %s" % (label, path))
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    lines = txt.splitlines()
    out.append("%s" % label)
    out.append("  %s   (%d B, %d lines, mtime %s)"
               % (p.name, p.stat().st_size, len(lines),
                  __import__("datetime").datetime.fromtimestamp(p.stat().st_mtime)
                  .strftime("%m-%d %H:%M")))
    out.append("=" * 104)
    out.append("  -- opening 12 lines --")
    for l in lines[:12]:
        if l.strip():
            out.append("    %s" % l.strip()[:116])
    # approval-state lines
    appr = [(i, l.strip()) for i, l in enumerate(lines, 1)
            if APPROVAL.search(l) and re.search(r"(GRANTED|NOT GRANTED|REJECT|UNRESOLVED|REQUESTED"
                                                r"|absent|missing|pending|awaiting)", l, re.I)]
    out.append("  -- approval-state lines (%d) --" % len(appr))
    for i, l in appr[:12]:
        out.append("    L%-5d %s" % (i, l[:112]))
    # status lines
    st = [(i, l.strip()) for i, l in enumerate(lines, 1) if STATUS.search(l) and l.strip().startswith(("|", "**", "#", "-"))]
    out.append("  -- status/verdict lines (%d shown of %d) --" % (min(14, len(st)), len(st)))
    for i, l in st[:14]:
        out.append("    L%-5d %s" % (i, l[:112]))
    out.append("")

open("staging/p11b24_extract.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
