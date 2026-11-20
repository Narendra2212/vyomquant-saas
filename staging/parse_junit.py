"""Cross-check a pytest summary line against its own JUnit XML.

The summary line and the XML are produced by the same run but counted differently -- pytest
counts outcomes, JUnit counts <testcase> elements and tags them -- so disagreement between
them is the cheapest available signal that a run was truncated, that a test errored during
collection, or that a figure was transcribed wrong.

Usage:  python staging/parse_junit.py staging/fullreg_FULLREG_P7.xml
"""

import collections
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]
tree = ET.parse(path)
root = tree.getroot()

suites = [root] if root.tag == "testsuite" else list(root)

attr_totals = collections.Counter()
for suite in suites:
    for key in ("tests", "failures", "errors", "skipped", "time"):
        value = suite.get(key)
        if value is None:
            continue
        attr_totals[key] += float(value) if key == "time" else int(value)

print("--- testsuite attributes (what the XML header claims) ---")
for key in ("tests", "failures", "errors", "skipped"):
    print(f"{key.upper()}={attr_totals[key]}")
print(f"TIME={attr_totals['time']:.2f}")

# Now count the elements themselves, which is the independent figure.
outcomes = collections.Counter()
failed_names = []
error_names = []
skip_reasons = collections.Counter()

for suite in suites:
    for case in suite.iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        children = {child.tag for child in case}
        if "failure" in children:
            outcomes["failed"] += 1
            failed_names.append(name)
        elif "error" in children:
            outcomes["error"] += 1
            error_names.append(name)
        elif "skipped" in children:
            skipped = case.find("skipped")
            kind = (skipped.get("type") or "").strip()
            if kind == "pytest.xfail":
                outcomes["xfailed"] += 1
            else:
                outcomes["skipped"] += 1
                reason = (skipped.get("message") or "").strip()
                skip_reasons[reason[:110]] += 1
        else:
            outcomes["passed"] += 1

print("\n--- counted <testcase> elements (the independent figure) ---")
total = sum(outcomes.values())
for key in ("passed", "failed", "error", "skipped", "xfailed"):
    print(f"{key.upper()}={outcomes[key]}")
print(f"TOTAL_TESTCASES={total}")

print("\n--- failures ---")
for name in sorted(failed_names):
    print(f"FAIL {name}")
for name in sorted(error_names):
    print(f"ERROR {name}")

print("\n--- top skip reasons ---")
for reason, count in skip_reasons.most_common(25):
    print(f"{count:5d}  {reason}")
