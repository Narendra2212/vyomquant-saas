"""Extract the full-regression counts from THREE independent sources and cross-check.

The rule for this engagement is that a suite-wide figure is either measured or not
reported. So the figure is taken from the authoritative source -- pytest's own summary
line -- and corroborated against two others. If they disagree, the disagreement is
printed rather than resolved by preference, because picking one would be inferring.

  1. pytest's terminal summary line        ("N passed, M failed, ... in Xs")
  2. the junit XML session attributes      (tests / failures / errors / skipped)
  3. a count of the per-test outcome lines in the -v output

Source 3 also yields a partial count if the run aborted, which is the only honest thing
to report in that case.

Run: .venv\\Scripts\\python.exe staging\\finalize_regression_counts.py
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

STAGING = Path(__file__).resolve().parent
TXT = STAGING / "fullreg_v_FINAL2.txt"
XML = STAGING / "fullreg_FINAL2.xml"

OUTCOMES = ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS")


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=encoding, errors="replace")
        except Exception:
            continue
    return ""


print("=" * 78)
print("SOURCE 1 - pytest's own summary line (AUTHORITATIVE)")
print("=" * 78)
summary = None
if not TXT.exists():
    print("  output file absent")
else:
    text = read_text(TXT)
    # The last line matching pytest's summary shape.
    pattern = re.compile(
        r"^=+\s*(?P<body>(?:\d+\s+\w+(?:,\s*)?)+)\s*in\s+[\d.]+s[^=]*=+\s*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        # -q/-v without the banner, e.g. "9 passed, 2 failed in 7.81s"
        plain = re.compile(
            r"^(?P<body>(?:\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|"
            r"warning|warnings|deselected|rerun)(?:,\s*)?)+)\s*in\s+[\d.]+s",
            re.MULTILINE,
        )
        matches = list(plain.finditer(text))
    if matches:
        summary = matches[-1].group("body").strip()
        print(f"  {summary}")
    else:
        print("  NO SUMMARY LINE FOUND -> the run did not reach the end of the session.")
        tail = [ln for ln in text.splitlines() if ln.strip()][-4:]
        print("  last non-empty lines:")
        for line in tail:
            print(f"    {line[:120]}")

print()
print("=" * 78)
print("SOURCE 2 - junit XML session attributes")
print("=" * 78)
if not XML.exists():
    print("  junit XML absent -> pytest writes it at session end, so the session did")
    print("  not complete.")
else:
    root = ET.parse(XML).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    for suite in suites:
        a = suite.attrib
        print(f"  tests={a.get('tests')} failures={a.get('failures')} "
              f"errors={a.get('errors')} skipped={a.get('skipped')} "
              f"time={a.get('time')}")
        # pytest records xfail as a <skipped type="pytest.xfail">.
        xfail = sum(
            1 for case in suite.iter("testcase")
            for sk in case.findall("skipped")
            if (sk.get("type") or "").endswith("xfail")
        )
        plain_skip = sum(
            1 for case in suite.iter("testcase")
            for sk in case.findall("skipped")
            if not (sk.get("type") or "").endswith("xfail")
        )
        cases = len(list(suite.iter("testcase")))
        fails = sum(1 for c in suite.iter("testcase") if c.findall("failure"))
        errs = sum(1 for c in suite.iter("testcase") if c.findall("error"))
        passed = cases - fails - errs - xfail - plain_skip
        print(f"  derived: testcases={cases} passed={passed} failed={fails} "
              f"errors={errs} skipped={plain_skip} xfailed={xfail}")

print()
print("=" * 78)
print("SOURCE 3 - per-test outcome lines in the -v output")
print("=" * 78)
if not TXT.exists():
    print("  output file absent")
else:
    text = read_text(TXT)
    counts = {}
    for name in OUTCOMES:
        counts[name] = len(re.findall(rf"\s{name}\s", text))
    total = sum(counts.values())
    for name in OUTCOMES:
        print(f"  {name:<8} {counts[name]}")
    print(f"  {'TOTAL':<8} {total}")
    collected = re.search(r"collected\s+(\d+)\s+items", text)
    if collected:
        n = int(collected.group(1))
        print(f"  collected = {n}")
        if total < n:
            print(f"  *** INCOMPLETE: {n - total} of {n} tests produced no outcome line.")
        elif total == n:
            print("  complete: every collected test produced an outcome line.")

print()
print("=" * 78)
print("VERDICT")
print("=" * 78)
if summary is None:
    print("  FULL REGRESSION INCOMPLETE - no pytest summary line was produced.")
    print("  Report only the partial per-outcome counts above, and do not infer the rest.")
    sys.exit(1)
print(f"  FULL REGRESSION COMPLETED. pytest summary: {summary}")
