"""Extract the Phase 7 full-regression counts for BOTH lanes and cross-check them.

Writes `staging/fullreg_phase7_counts.json`, which `update_audit_json.py` reads on its next
invocation. Nothing is asserted that is not read back from each run's own output.

TWO LANES, REPORTED SIDE BY SIDE, AND THAT IS THE POINT:

  CI lane (FULLREG_P7)      no DATABASE_URL and no AERORA_TEST_DATABASE_URL -- reproduces
                            01-pr-check.yml's "Run Unit Tests" job, so the figures are
                            comparable to the Phase 5 and Phase 6 baselines. Every
                            PostgreSQL-only suite SKIPS in this lane.
  PostgreSQL lane
  (FULLREG_P7PG)            AERORA_TEST_DATABASE_URL set from staging/pg_dsn.txt, so the
                            PostgreSQL suites actually execute.

Reporting only the first would be reporting a pass on tests that never ran, which the brief
forbids. Reporting only the second would not be comparable to the baselines. The DIFFERENCE
between them is the infrastructure-gated surface, and it is quantified here.

Each lane is cross-checked three ways:
  1. pytest's own summary line -- authoritative.
  2. the junit XML session attributes -- independent of the summary line.
  3. the arithmetic: failed + passed + skipped + xfailed + xpassed == collected.

The third check exists because the first two can agree and still be misread: junit's
`skipped` folds xfailed in, and its element-level pass count counts xpassed as a pass, so
the two sources disagree BY DESIGN and the disagreement has to be EXPLAINED rather than
averaged.

Run:  .venv\\Scripts\\python.exe staging\\finalize_phase7_counts.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import xml.etree.ElementTree as ET

STAGING = pathlib.Path(__file__).resolve().parent
OUT = STAGING / "fullreg_phase7_counts.json"

COMMAND = (
    "pytest tests -q -p no:cacheprovider -o addopts= "
    "--timeout=1800 --timeout-method=thread --tb=line -rfE"
)

#: Phase 6's final figures, for the delta.
BASELINE_PHASE_6 = {
    "collected": 13534,
    "passed": 13082,
    "failed": 9,
    "skipped": 434,
    "xfailed": 6,
    "xpassed": 3,
}


def parse_summary(text: str) -> dict:
    """The last `N passed, M failed … in Xs` line pytest prints.

    `-q` prints the summary WITHOUT the `=====` rule that `-v` uses, so requiring an '='
    on the line silently matches nothing and reports every count as zero. That defect was
    caught in Phase 6 by the junit cross-check, which is why the cross-check exists.
    """
    counts: dict = {}
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
    duration = re.search(r"in ([\d.]+)s", line)
    if duration:
        counts["duration_seconds"] = float(duration.group(1))
    counts["_summary_line"] = line.strip()
    return counts


def parse_collected(text: str) -> int | None:
    """pytest's own `collected N items` line, if it printed one.

    `-q` SUPPRESSES IT. So this returns None for both of these runs, and the collected
    figure has to come from somewhere else - see `lane()`, which falls back to the junit
    `tests` attribute. That keeps the cross-check honest (two independent sources, the
    summary line's arithmetic against the XML) instead of comparing the arithmetic to a
    None and reporting a failure that is really a missing input.
    """
    hits = re.findall(r"collected (\d+) items", text)
    return int(hits[-1]) if hits else None


def parse_junit(xml: pathlib.Path) -> dict | None:
    """Both the header attributes AND an independent element count.

    Returns None rather than raising when the file is absent or not yet complete: pytest
    writes the junit XML at the END of the session, so a run still in flight has either no
    file or a truncated one, and a parse error there says nothing about the run.
    """
    if not xml.exists():
        return None
    try:
        root = ET.parse(xml).getroot()
    except ET.ParseError as exc:
        return {"unparseable": f"{type(exc).__name__}: {exc}"}
    suites = [root] if root.tag == "testsuite" else list(root)

    header: collections.Counter = collections.Counter()
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            value = suite.get(key)
            if value is not None:
                header[key] += int(value)

    outcomes: collections.Counter = collections.Counter()
    skip_reasons: collections.Counter = collections.Counter()
    for suite in suites:
        for case in suite.iter("testcase"):
            children = {child.tag for child in case}
            if "failure" in children:
                outcomes["failed"] += 1
            elif "error" in children:
                outcomes["error"] += 1
            elif "skipped" in children:
                node = case.find("skipped")
                if (node.get("type") or "").strip() == "pytest.xfail":
                    outcomes["xfailed"] += 1
                else:
                    outcomes["skipped"] += 1
                    skip_reasons[(node.get("message") or "").strip()[:110]] += 1
            else:
                outcomes["passed"] += 1

    return {
        "header_attributes": dict(header),
        "counted_elements": dict(outcomes),
        "total_testcases": sum(outcomes.values()),
        "top_skip_reasons": skip_reasons.most_common(12),
    }


def parse_failed_ids(text: str) -> list[str]:
    return sorted(
        {
            line.split(" ", 1)[1].split(" - ")[0].strip()
            for line in text.splitlines()
            if line.startswith("FAILED ")
        }
    )


def lane(nonce: str, label: str, description: str) -> dict:
    raw = STAGING / f"fullreg_raw_{nonce}.txt"
    xml = STAGING / f"fullreg_{nonce}.xml"
    done = STAGING / f"fullreg_done_{nonce}.txt"

    print(f"\n================ {label} ({nonce}) ================")
    if not raw.exists():
        print(f"ABSENT: {raw}")
        return {"status": "NOT RUN", "lane": label}

    try:
        text = raw.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        # Windows holds the file while the wrapper's Add-Content pipeline is writing it,
        # which is itself the signal that the run is still in flight.
        print(f"IN FLIGHT: {raw.name} is locked by the writing pipeline")
        return {
            "lane": label,
            "description": description,
            "status": "STILL RUNNING AT THE TIME THIS SCRIPT LAST RAN",
            "artifact": f"staging/fullreg_raw_{nonce}.txt",
        }

    print(
        "DONE_MARKER  = "
        + (
            done.read_text(encoding="utf-8").strip()
            if done.exists()
            else "ABSENT -- the run has not finished; counts below are partial"
        )
    )

    summary = parse_summary(text)
    collected_printed = parse_collected(text)
    junit = parse_junit(xml) if done.exists() else None
    failed_ids = parse_failed_ids(text)

    #: `-q` suppresses pytest's `collected N items` line, so the collected figure comes
    #: from the junit `tests` attribute when pytest did not print one. Recorded with its
    #: source, because "collected" sourced from the same place as the cross-check would
    #: make the cross-check vacuous.
    collected = collected_printed
    collected_source = "pytest 'collected N items' line"
    if collected is None and junit and "header_attributes" in junit:
        collected = junit["header_attributes"].get("tests")
        collected_source = "junit testsuite@tests (pytest -q printed no collected line)"

    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    xfailed = summary.get("xfailed", 0)
    xpassed = summary.get("xpassed", 0)
    errors = summary.get("error", 0)
    total = passed + failed + skipped + xfailed + xpassed

    print(f"SUMMARY_LINE = {summary.get('_summary_line')}")
    print(f"COLLECTED    = {collected}   [{collected_source}]")
    print(f"PASSED       = {passed}")
    print(f"FAILED       = {failed}")
    print(f"ERRORS       = {errors}")
    print(f"SKIPPED      = {skipped}")
    print(f"XFAILED      = {xfailed}")
    print(f"XPASSED      = {xpassed}")
    print(f"DURATION     = {summary.get('duration_seconds')}s")
    print(f"ARITHMETIC   = {failed}+{passed}+{skipped}+{xfailed}+{xpassed} = {total}")
    print(f"MATCHES_COLLECTED = {collected == total}")

    reconciled = None
    if junit and "header_attributes" in junit:
        hdr = junit["header_attributes"]
        els = junit["counted_elements"]
        checks = {
            "junit.tests == collected": hdr.get("tests") == collected,
            "junit.failures == failed": hdr.get("failures") == failed,
            "junit.errors == errors": hdr.get("errors") == errors,
            "junit.skipped == skipped + xfailed": hdr.get("skipped")
            == skipped + xfailed,
            "counted elements total == collected": junit["total_testcases"] == collected,
            "counted passed == passed + xpassed": els.get("passed")
            == passed + xpassed,
            "counted failed == failed": els.get("failed") == failed,
        }
        reconciled = checks
        print("JUNIT CROSS-CHECK:")
        for name, ok in checks.items():
            print(f"  {'OK  ' if ok else 'FAIL'} {name}")

    print(f"FAILED_IDS   = {len(failed_ids)}")
    for fid in failed_ids:
        print(f"  {fid}")

    return {
        "lane": label,
        "description": description,
        "status": "COMPLETED"
        if done.exists() and collected and collected == total
        else ("INCOMPLETE" if not done.exists() else "INCONSISTENT"),
        "collected": collected,
        "collected_source": collected_source,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "duration_seconds": summary.get("duration_seconds"),
        "command": COMMAND,
        "artifact": f"staging/fullreg_raw_{nonce}.txt; junit staging/fullreg_{nonce}.xml",
        "summary_line": summary.get("_summary_line"),
        "arithmetic_check": (
            f"{failed} + {passed} + {skipped} + {xfailed} + {xpassed} = {total}"
            f" {'==' if collected == total else '!='} collected {collected}"
        ),
        "junit": junit,
        "junit_cross_check": reconciled,
        "junit_reconciliation": (
            "junit's `skipped` header attribute folds xfailed in, and its element-level "
            "pass count counts xpassed as a pass. So junit.skipped should equal "
            "skipped + xfailed, and the counted passed elements should equal "
            "passed + xpassed. The two sources disagree BY DESIGN; the reconciliation is "
            "the check, not the agreement."
        ),
        "failed_ids": failed_ids,
    }


def main() -> int:
    ci = lane(
        "FULLREG_P7B",
        "CI lane (AUTHORITATIVE)",
        "No DATABASE_URL and no AERORA_TEST_DATABASE_URL. Reproduces "
        "01-pr-check.yml's 'Run Unit Tests' job, so the figures are comparable to the "
        "Phase 5 and Phase 6 baselines. Every PostgreSQL-only suite SKIPS here. Run "
        "against the FROZEN code, after the ORM skip-guard, config-reporting and LIB-04 "
        "changes.",
    )
    pg = lane(
        "FULLREG_P7BPG",
        "PostgreSQL lane (AUTHORITATIVE)",
        "AERORA_TEST_DATABASE_URL set from staging/pg_dsn.txt against a local "
        "PostgreSQL 16.2, so the PostgreSQL-only suites actually execute rather than "
        "skipping. Run against the FROZEN code.",
    )

    #: The two earlier runs are kept because they are not superseded evidence - the
    #: PostgreSQL one is what FOUND LIB-04, and the CI one is the only measurement of the
    #: code before the ORM skip guard was corrected.
    ci_pre = lane(
        "FULLREG_P7",
        "CI lane (intermediate, pre-fix)",
        "The first CI-lane run. Predates the ORM skip-guard fix, the config-reporting fix "
        "and LIB-04. Its four test_orm_models_match_provisioned_schema failures are the "
        "stale-DSN defect, not schema findings.",
    )
    pg_pre = lane(
        "FULLREG_P7PG",
        "PostgreSQL lane (intermediate, pre-fix)",
        "The first PostgreSQL-lane run, and the one that FOUND LIB-04: eleven tests across "
        "four files answered 500 on the clone/unpublish routes because a dead client "
        "leaked from a save/restore race on a module global across two threads in "
        "tests/test_lib01_clone_strategy_postgres.py, and the resulting "
        "psycopg2.InterfaceError escaped a handler narrowed to _PostgrestAPIError. Every "
        "one of those files passed in isolation and in every targeted grouping.",
    )

    record = {
        "lanes": {"ci": ci, "postgresql": pg},
        "intermediate_lanes": {"ci": ci_pre, "postgresql": pg_pre},
        "why_two_lanes": (
            "Reporting only the CI lane would report a pass on tests that never ran, "
            "because that lane skips every PostgreSQL-only suite by design. Reporting only "
            "the PostgreSQL lane would not be comparable to the Phase 5/6 baselines. The "
            "difference between the two IS the infrastructure-gated surface."
        ),
        "baseline_phase_6": BASELINE_PHASE_6,
    }

    if ci.get("collected"):
        record["delta_vs_phase_6_ci_lane"] = {
            "collected": ci["collected"] - BASELINE_PHASE_6["collected"],
            "passed": ci["passed"] - BASELINE_PHASE_6["passed"],
            "failed": ci["failed"] - BASELINE_PHASE_6["failed"],
            "skipped": ci["skipped"] - BASELINE_PHASE_6["skipped"],
        }

    if ci.get("collected") and pg.get("collected"):
        record["infrastructure_gated_surface"] = {
            "skipped_in_ci_lane": ci["skipped"],
            "skipped_in_postgresql_lane": pg["skipped"],
            "skips_converted_to_runs": ci["skipped"] - pg["skipped"],
            "note": "The reduction is the number of tests the CI lane could not measure "
                    "and the PostgreSQL lane did. A test that still skips in the "
                    "PostgreSQL lane needs something else - Redis, a running HTTP server, "
                    "a built frontend bundle - and those are named in the top skip reasons.",
        }

    OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nWROTE {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
