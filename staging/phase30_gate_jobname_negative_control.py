"""Phase 30 negative control: does the Phase 18 gate's mandatory-job config match reality?

WHY THIS EXISTS
---------------
`staging/phase18_release_gate.py` is the artifact Phases 18, 23, 25, 26 and 28 all recommend
promoting to production (requests "G-3" / "④"). It closes a real gap the installed verifier has:
it checks that each MANDATORY job inside a required workflow individually concluded `success`,
so a run-level green with a skipped mandatory job is refused.

Its own suite passes 28/28. But every one of those cases builds its job fixtures with
`_job("sast-scan", ...)` -- i.e. the fixture's job *name* is the string from
`REQUIRED_WORKFLOWS`. So the suite can only ever confirm that the matcher matches itself. It
cannot detect a mismatch between the configured identifiers and what the GitHub API actually
reports for THIS repository's workflows.

That is what this control tests, and it is a question no prior phase asked.

THE MECHANISM UNDER TEST
------------------------
`_assert_mandatory_jobs_passed` builds `by_name = {job["name"]: job}` and then looks up each
entry of `mandatory`. For the GitHub Actions API, a job's `name` is the job's `name:` field when
one is present, and only falls back to the job id (the YAML key) when `name:` is absent. The
prototype's own comment says exactly this.

`REQUIRED_WORKFLOWS` is configured with job *keys* (`sast-scan`, `container-security`, ...).
Every one of those jobs in this repository declares a `name:`. So this control parses the real
workflow YAML, constructs an all-green job list using the names the API would actually report,
and asks the gate to admit it.

READ-ONLY. Loads `phase18_release_gate.py` by path without modifying it, parses workflow YAML,
and makes no network call, no git write and no file write outside this script's own stdout.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
GATE_PATH = REPO / "staging" / "phase18_release_gate.py"
WORKFLOWS = REPO / ".github" / "workflows"


def load_gate():
    """Import the prototype by path, leaving the file untouched."""
    spec = importlib.util.spec_from_file_location("p18_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api_job_names(workflow_file: str) -> dict:
    """The job names the GitHub API would report, keyed by YAML job id.

    The API reports `name:` when present, else the job id. Mirrors the prototype's own
    documented assumption so the comparison is apples to apples.
    """
    data = yaml.safe_load((WORKFLOWS / workflow_file).read_text(encoding="utf-8"))
    return {key: (body.get("name") or key) for key, body in data["jobs"].items()}


def green(name: str) -> dict:
    return {"name": name, "status": "completed", "conclusion": "success"}


def main() -> int:
    gate = load_gate()
    workflow_by_display_name = {}
    for filename in sorted(p.name for p in WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))
        workflow_by_display_name[data.get("name")] = filename

    print("=" * 78)
    print("CONFIGURED MANDATORY IDENTIFIERS vs WHAT THE API REPORTS")
    print("=" * 78)

    failures = []
    for workflow_name, mandatory in gate.REQUIRED_WORKFLOWS.items():
        filename = workflow_by_display_name.get(workflow_name)
        if filename is None:
            print("\n%r -- NO WORKFLOW FILE DECLARES THIS NAME" % workflow_name)
            failures.append((workflow_name, "workflow name not found", list(mandatory)))
            continue

        real = api_job_names(filename)
        real_names = set(real.values())
        print("\n%r  (%s)" % (workflow_name, filename))
        for key, reported in real.items():
            print("    yaml key %-28s -> API reports %r" % (key, reported))

        missing = [m for m in mandatory if m not in real_names]
        print("    configured mandatory : %s" % list(mandatory))
        print("    MATCHED              : %s" % [m for m in mandatory if m in real_names])
        print("    UNMATCHABLE          : %s" % missing)
        if missing:
            failures.append((workflow_name, filename, missing))

    print("\n" + "=" * 78)
    print("BEHAVIOURAL PROOF: feed the gate an ALL-GREEN run using REAL API job names")
    print("=" * 78)

    for workflow_name, mandatory in gate.REQUIRED_WORKFLOWS.items():
        filename = workflow_by_display_name.get(workflow_name)
        if filename is None:
            continue
        real = api_job_names(filename)
        # Every job present and green -- the most releasable run possible.
        jobs = [green(n) for n in real.values()]
        print("\n%r -- all %d jobs present, status=completed, conclusion=success"
              % (workflow_name, len(jobs)))
        try:
            gate._assert_mandatory_jobs_passed(jobs, workflow_name, mandatory, run_id=999)
            print("    RESULT: ADMITTED")
        except gate.GateRefusal as refusal:
            print("    RESULT: *** REFUSED ***")
            print("    %s" % str(refusal).replace("\n", "\n    "))

    print("\n" + "=" * 78)
    print("CONTROL: same call, but job names forced to the CONFIGURED identifiers")
    print("=" * 78)
    print("If these are admitted while the real names are refused, the matcher is sound and")
    print("the CONFIGURATION is what is wrong -- which is the distinction that matters.")

    for workflow_name, mandatory in gate.REQUIRED_WORKFLOWS.items():
        jobs = [green(m) for m in mandatory]
        try:
            gate._assert_mandatory_jobs_passed(jobs, workflow_name, mandatory, run_id=999)
            print("\n%r -- RESULT: ADMITTED (matcher works on its own fixtures)" % workflow_name)
        except gate.GateRefusal as refusal:
            print("\n%r -- RESULT: REFUSED -- %s" % (workflow_name, refusal))

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    if failures:
        total = sum(len(m) for _, _, m in failures)
        print("DEFECT CONFIRMED: %d configured mandatory job identifier(s) across %d workflow(s)"
              % (total, len(failures)))
        print("cannot match any job name this repository's workflows actually report.")
        print("")
        print("Direction of failure: FAIL-CLOSED. The gate refuses a fully green candidate")
        print("rather than admitting a bad one, so this is not a security hole -- but as")
        print("configured the gate would block every release, including correct ones, and its")
        print("28 passing tests cannot detect it because their fixtures carry the same wrong")
        print("names as the configuration.")
        return 1
    print("No mismatch found: every configured mandatory identifier matches a real job name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
