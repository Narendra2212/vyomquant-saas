"""Summarise the Phase 11B-A11 diagnostic JUnit XML artifacts, per file, per test."""
import glob
import os
import xml.etree.ElementTree as ET

lines = []
for path in sorted(glob.glob("staging/p11b13_diag/*.xml")):
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    a = suite.attrib
    lines.append("=" * 76)
    lines.append(os.path.basename(path))
    lines.append(
        "  tests=%s failures=%s errors=%s skipped=%s time=%s"
        % (a.get("tests"), a.get("failures"), a.get("errors"),
           a.get("skipped"), a.get("time"))
    )
    for tc in suite.findall("testcase"):
        kind = "PASSED"
        msg = ""
        for ch in tc:
            if ch.tag in ("failure", "error"):
                kind = ch.tag.upper()
                msg = (ch.get("message") or "").strip().replace("\n", " ")[:170]
            elif ch.tag == "skipped":
                kind = "SKIPPED"
                msg = (ch.get("message") or "").strip().replace("\n", " ")[:170]
        marker = "  " if kind == "PASSED" else ">>"
        lines.append("%s %-8s %s" % (marker, kind, tc.get("name")))
        if msg:
            lines.append("            %s" % msg)

out = "\n".join(lines)
open("staging/p11b13_junit_summary.txt", "w", encoding="utf-8").write(out)
print(out)
