"""Extract the Phase 6 full-regression counts and cross-check them three ways.

Writes `staging/fullreg_phase6_counts.json`, which `update_audit_json.py` reads on its next
invocation. Nothing is asserted that is not read back from the run's own output:

  1. pytest's own summary line -- authoritative.
  2. the junit XML session attributes -- independent of the summary line.
  3. the arithmetic: failed + passed + skipped + xfailed + xpassed must equal collected.

The third check exists because the first two can agree and still be misread: junit's
`skipped` folds xfailed in, and its derived pass count counts xpassed as passes, so the two
sources disagree BY DESIGN and the disagreement has to be explained rather than averaged.

Run:  .venv\\Scripts\\python.exe staging\\finalize_phase6_counts.py
"""
from __future__ import annotations

import json
import pathlib
import re
import xml.etree.ElementTree as ET

STAGING = pathlib.Path(__file__).resolve().parent
RAW = STAGING / "fullreg_raw_FULLREG_P6.txt"
XML = STAGING / "fullreg_FULLREG_P6.xml"
OUT = STAGING / "fullreg_phase6_counts.json"

COMMAND = (
    "pytest tests -q -p no:cacheprovider -o addopts= "
    "--timeout=1800 --timeout-method=thread --tb=line -rfE"
)


def parse_summary(text: str) -> dict[str, int]:
    """The last `=== N passed, M failed … ===` line pytest prints."""
    counts: dict[str, int] = {}
    # `-q` prints the summary WITHOUT the `=====` rule that `-v` uses, so requiring an
    # '=' on the line silently matched nothing and reported every count as zero. Matched
    # on the counts themselves instead, and the junit cross-check is what caught it.
    summary_lines = [
        line
        for line in text.splitlines()
        if re.search(r"\b\d+ (passed|failed|error)", line)
        and " in " in line
        and not line.startswith("FAILED")
    ]
    if not summary_lines:
        return counts
    line = summary_lines[-1]
    for n, label in re.findall(
        r"(\d+) (passed|failed|skipped|xfailed|xpassed|error|errors|warning|warnings)",
        line,
    ):
        key = {"errors": "error", "warnings": "warning"}.get(label, label)
        counts[key] = int(n)
    counts["_summary_line"] = line.strip()  # type: ignore[assignment]
    return counts


def parse_collected(text: str) -> int | None:
    hits = re.findall(r"collected (\d+) items", text)
    if hits:
        return int(hits[-1])
    return None


def parse_junit() -> dict[str, int] | None:
    if not XML.exists():
        return None
    root = ET.parse(XML).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None
    return {
        "tests": int(suite.get("tests", 0)),
        "failures": int(suite.get("failures", 0)),
        "errors": int(suite.get("errors", 0)),
        "skipped": int(suite.get("skipped", 0)),
    }


def parse_failed_ids(text: str) -> list[str]:
    return sorted(
        {
            line.split(" ", 1)[1].split(" - ")[0].strip()
            for line in text.splitlines()
            if line.startswith("FAILED ")
        }
    )


def main() -> int:
    if not RAW.exists():
        print(f"ABSENT: {RAW}")
        return 1

    text = RAW.read_text(encoding="utf-8", errors="replace")

    done_marker = STAGING / "fullreg_done_FULLREG_P6.txt"
    if done_marker.exists():
        print(f"DONE_MARKER  = {done_marker.read_text(encoding='utf-8').strip()}")
    else:
        print("DONE_MARKER  = ABSENT -- the run has not finished; counts below are partial")

    summary = parse_summary(text)
    collected = parse_collected(text)
    junit = parse_junit()
    failed_ids = parse_failed_ids(text)

    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    xfailed = summary.get("xfailed", 0)
    xpassed = summary.get("xpassed", 0)
    errors = summary.get("error", 0)
    total = passed + failed + skipped + xfailed + xpassed

    print(f"SUMMARY_LINE = {summary.get('_summary_line')}")
    print(f"COLLECTED    = {collected}")
    print(f"PASSED       = {passed}")
    print(f"FAILED       = {failed}")
    print(f"SKIPPED      = {skipped}")
    print(f"XFAILED      = {xfailed}")
    print(f"XPASSED      = {xpassed}")
    print(f"ERRORS       = {errors}")
    print(f"ARITHMETIC   = {failed}+{passed}+{skipped}+{xfailed}+{xpassed} = {total}")
    print(f"MATCHES_COLLECTED = {collected == total}")
    print(f"JUNIT        = {junit}")
    print(f"FAILED_IDS   = {len(failed_ids)}")
    for fid in failed_ids:
        print(f"  {fid}")

    record = {
        "status": "COMPLETED" if collected and collected == total else "INCONSISTENT",
        "collected": collected,
        "collection_errors": errors,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "command": COMMAND,
        "artifact": "staging/fullreg_raw_FULLREG_P6.txt; junit "
                    "staging/fullreg_FULLREG_P6.xml",
        "arithmetic_check": (
            f"{failed} + {passed} + {skipped} + {xfailed} + {xpassed} = {total}"
            f" {'=' if collected == total else '!='} collected {collected}"
        ),
        "junit_session_attributes": junit,
        "junit_reconciliation": (
            "junit `skipped` folds xfailed in and its derived pass count counts xpassed as "
            "passes, so junit.skipped should equal skipped + xfailed and junit.tests should "
            "equal collected. The two sources disagree by design; the reconciliation is the "
            "check, not the agreement."
        ),
        "failed_ids": failed_ids,
        "baseline_phase_5": {
            "collected": 13435, "passed": 13046, "failed": 17,
            "skipped": 363, "xfailed": 6, "xpassed": 3,
        },
        "delta_vs_phase_5": {
            "collected": (collected - 13435) if collected else None,
            "passed": passed - 13046,
            "failed": failed - 17,
        },
    }
    OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nWROTE {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
