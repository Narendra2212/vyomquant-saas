"""Phase 31 PROPOSAL ARTIFACT — the release gate's identity contract, tested against reality.

NOT INSTALLED. No grant exists for `.github/workflows/**`, `scripts/**` or `tests/**`, and
`staging/phase18_release_gate.py` belongs to another session, so nothing governed is modified.
This module is loaded by path and asserts against the LIVE workflow YAML and REAL captured
GitHub API payloads. On a grant it moves to `tests/test_release_gate_identity_contract.py`,
where `pytest.ini`'s `testpaths = tests` and `python_files = test_*.py` will collect it.

WHAT PHASE 30 ESTABLISHED, AND WHAT THIS ADDS
---------------------------------------------
Phase 30 proved by execution that `phase18_release_gate.py`'s `REQUIRED_WORKFLOWS` lists job
YAML *keys* while the verifier matches on the API's job *display name*, so all nine configured
mandatory identifiers were unmatchable and an all-green run was refused. It proved the DEFECT.

This module proves the FIX, and finds a second defect of the same class that Phase 30 did not
reach.

THE FIX SHAPE, AND WHY THIS ONE
-------------------------------
Three options were considered:

  (a) replace the keys with display names. Rejected as the whole answer: it silently re-breaks
      the moment someone edits a `name:` field, which is exactly how the current defect arose.
  (b) match on job keys. Not possible -- the `/jobs` API response carries no YAML key, only
      `name`. There is nothing to match against.
  (c) an EXPLICIT key -> expected-display-name mapping, plus a test that resolves it against
      the live YAML. A rename then fails a test naming both strings rather than silently
      disarming the gate.

(c) is implemented here as `REQUIRED_JOBS`. It is the shape the brief asks for: deterministic,
fail-closed on ambiguity, and preserving the mandatory/advisory distinction.

THE SECOND DEFECT -- TWO OF THE GATE'S OWN SETTINGS ARE MUTUALLY UNSATISFIABLE
-----------------------------------------------------------------------------
`phase18_release_gate.py` sets `ALLOWED_BRANCHES = ("main", "master")` and
`_assert_run_is_admissible` refuses any run whose `head_branch` is outside it. It also lists
`01 PR Check` in `REQUIRED_WORKFLOWS`.

But `01-pr-check.yml` is `on: [pull_request]` only, and a `pull_request` run reports
`head_branch` = the PR's own feature branch, never `main`. So no `01 PR Check` run can ever
satisfy the branch allow-list. The two settings cannot both be met.

`test_branch_allowlist_and_pr_only_workflow_are_mutually_unsatisfiable` demonstrates this
against a REAL captured run record for `6c5660a1`, a commit that is genuinely an ancestor of
`main` and genuinely has a `01 PR Check` run -- the natural experiment that makes the
contradiction observable rather than theoretical.

EVIDENCE PROVENANCE
-------------------
`staging/phase21_sec_jobs.json`      real `/runs/38129699454/jobs` at `ecd23a9e`
`staging/phase31_6c5660a1_runs.json` real `/runs?head_sha=6c5660a1...`
Both are read-only captures. No network call is made by this module.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
GATE_PATH = REPO / "staging" / "phase18_release_gate.py"
WORKFLOWS = REPO / ".github" / "workflows"
SEC_JOBS = REPO / "staging" / "phase21_sec_jobs.json"
ANCESTOR_RUNS = REPO / "staging" / "phase31_6c5660a1_runs.json"

# ---------------------------------------------------------------------------------------------
# THE PROPOSED CORRECTION.
#
# workflow display name -> {stable YAML job key: expected API-reported display name}
#
# Both halves are asserted against the live YAML by
# `test_every_required_job_key_exists` and `test_every_expected_display_name_is_current`.
# A `name:` edit breaks those two tests with both strings in the message, instead of silently
# disarming the gate.
# ---------------------------------------------------------------------------------------------
REQUIRED_JOBS: dict = {
    "05 Security": {
        "sast-scan": "Bandit SAST & Dependency Security Scan",
        "container-security": "Trivy Container Vulnerability Scanner",
        "iac-scan": "Trivy IaC Misconfiguration & Secret Scan",
    },
    "01 PR Check": {
        "static-audit": "Code Quality & Static Audit",
        "unit-tests": "Unit & Integration Tests",
        "paper-mode-tests": "Execution Tests (production posture)",
        "database-tests": "Database Tests (PostgreSQL)",
        "docker-check": "Docker Build Validation",
        "frontend-tests": "Frontend Build & Unit Tests",
    },
}

#: Preserved verbatim from the prototype, with its reasons. `format-check` is
#: `continue-on-error: true` with nothing depending on it; `provisioning-and-isolation`
#: provisions PostgreSQL and whether it is release-mandatory is an owner decision.
ADVISORY_JOB_KEYS = ("format-check", "provisioning-and-isolation")

WORKFLOW_FILE = {"05 Security": "05-security.yml", "01 PR Check": "01-pr-check.yml"}


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("p18_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(workflow_name: str) -> dict:
    return yaml.safe_load(
        (WORKFLOWS / WORKFLOW_FILE[workflow_name]).read_text(encoding="utf-8")
    )


def _api_names(workflow_name: str) -> dict:
    """{job key: the name the API reports} -- `name:` when present, else the key."""
    jobs = _workflow(workflow_name)["jobs"]
    return {key: (body.get("name") or key) for key, body in jobs.items()}


def _job(name: str, conclusion: str = "success", status: str = "completed") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def _resolved(workflow_name: str) -> tuple:
    return tuple(REQUIRED_JOBS[workflow_name].values())


# =============================================================================================
# 1. The mapping must correspond to the live workflows. This is the test Phase 30 showed absent.
# =============================================================================================

@pytest.mark.parametrize("workflow_name", sorted(REQUIRED_JOBS))
def test_every_required_job_key_exists(workflow_name):
    """Each configured key must be a real job id in the workflow file."""
    declared = set(_workflow(workflow_name)["jobs"])
    for key in REQUIRED_JOBS[workflow_name]:
        assert key in declared, (
            "%s lists mandatory job key %r, which does not exist in %s. Declared: %s"
            % (workflow_name, key, WORKFLOW_FILE[workflow_name], sorted(declared))
        )


@pytest.mark.parametrize("workflow_name", sorted(REQUIRED_JOBS))
def test_every_expected_display_name_is_current(workflow_name):
    """Each expected display name must equal what the API would report today.

    This is the assertion whose absence let the Phase 18 configuration drift. It fails with
    both strings named, so a rename is a one-line fix rather than a silent disarm.
    """
    actual = _api_names(workflow_name)
    for key, expected in REQUIRED_JOBS[workflow_name].items():
        assert actual[key] == expected, (
            "%s job %r reports display name %r but the gate expects %r. The GitHub API matches "
            "on the display name, so this mismatch would make the mandatory job unmatchable and "
            "the gate would refuse every run. Update the mapping or revert the `name:` change."
            % (workflow_name, key, actual[key], expected)
        )


@pytest.mark.parametrize("workflow_name", sorted(REQUIRED_JOBS))
def test_advisory_jobs_are_not_mandatory(workflow_name):
    """An advisory job must never appear in the mandatory set."""
    advisory_names = {
        _api_names(workflow_name)[k]
        for k in ADVISORY_JOB_KEYS
        if k in _api_names(workflow_name)
    }
    assert not advisory_names & set(_resolved(workflow_name)), (
        "advisory job(s) %s leaked into %s's mandatory set"
        % (advisory_names & set(_resolved(workflow_name)), workflow_name)
    )


def test_the_phase18_configuration_is_still_unmatchable(gate):
    """Pins Phase 30's finding so the regression cannot silently return.

    If someone fixes `phase18_release_gate.py` this test fails loudly and should be deleted --
    which is the correct signal, not a nuisance.
    """
    unmatchable = []
    for workflow_name, mandatory in gate.REQUIRED_WORKFLOWS.items():
        if workflow_name not in REQUIRED_JOBS:
            continue
        real = set(_api_names(workflow_name).values())
        unmatchable += [m for m in mandatory if m not in real]
    assert len(unmatchable) == 9, (
        "expected Phase 30's 9 unmatchable identifiers, found %d: %s"
        % (len(unmatchable), unmatchable)
    )


# =============================================================================================
# 2. With the corrected mapping, the verifier must behave. Real payloads where available.
# =============================================================================================

def test_corrected_mapping_admits_an_all_green_run(gate):
    """The case Phase 30 showed being refused must now be admitted."""
    for workflow_name in REQUIRED_JOBS:
        jobs = [_job(n) for n in _api_names(workflow_name).values()]
        gate._assert_mandatory_jobs_passed(
            jobs, workflow_name, _resolved(workflow_name), run_id=1
        )


def test_corrected_mapping_refuses_the_real_ecd23a9e_security_run(gate):
    """REAL payload: run 38129699454 -- 1 success, 2 failures. Must refuse."""
    payload = json.loads(SEC_JOBS.read_text(encoding="utf-8-sig"))
    jobs = payload["jobs"]
    assert len(jobs) == 3, "evidence capture changed shape"
    assert sum(1 for j in jobs if j["conclusion"] == "failure") == 2

    with pytest.raises(gate.GateRefusal) as refusal:
        gate._assert_mandatory_jobs_passed(
            jobs, "05 Security", _resolved("05 Security"), run_id=38129699454
        )
    assert "Bandit SAST & Dependency Security Scan" in str(refusal.value)


@pytest.mark.parametrize(
    "bad_conclusion", ["failure", "skipped", "cancelled", "neutral", "timed_out", "stale"]
)
def test_any_non_success_mandatory_conclusion_is_refused(gate, bad_conclusion):
    names = list(_api_names("05 Security").values())
    jobs = [_job(names[0], bad_conclusion)] + [_job(n) for n in names[1:]]
    with pytest.raises(gate.GateRefusal):
        gate._assert_mandatory_jobs_passed(
            jobs, "05 Security", _resolved("05 Security"), run_id=2
        )


def test_an_unfinished_mandatory_job_is_refused(gate):
    names = list(_api_names("05 Security").values())
    jobs = [_job(names[0], conclusion=None, status="in_progress")] + \
           [_job(n) for n in names[1:]]
    with pytest.raises(gate.GateRefusal):
        gate._assert_mandatory_jobs_passed(
            jobs, "05 Security", _resolved("05 Security"), run_id=3
        )


def test_a_missing_mandatory_job_is_refused(gate):
    names = list(_api_names("05 Security").values())
    with pytest.raises(gate.GateRefusal) as refusal:
        gate._assert_mandatory_jobs_passed(
            [_job(n) for n in names[1:]], "05 Security", _resolved("05 Security"), run_id=4
        )
    assert "contains no job named" in str(refusal.value)


def test_advisory_success_cannot_substitute_for_a_missing_mandatory_job(gate):
    """A green advisory job must not let an absent mandatory job through."""
    jobs = [_job("Format Backlog (report only)")]
    with pytest.raises(gate.GateRefusal):
        gate._assert_mandatory_jobs_passed(
            jobs, "01 PR Check", _resolved("01 PR Check"), run_id=5
        )


# =============================================================================================
# 3. NEGATIVE CONTROLS. If these pass without their assertion, the green tests above are vacuous.
# =============================================================================================

def test_negative_control_a_renamed_display_name_is_detected():
    """Corrupt one expected name; the mapping check must reject it."""
    corrupted = {"sast-scan": "Bandit SAST & Dependency Security Scan -- RENAMED"}
    actual = _api_names("05 Security")
    mismatches = [k for k, v in corrupted.items() if actual[k] != v]
    assert mismatches == ["sast-scan"], (
        "the mapping check cannot detect a renamed display name, so "
        "test_every_expected_display_name_is_current is vacuous"
    )


def test_negative_control_the_mandatory_assertion_is_load_bearing(gate):
    """Neuter the job assertion; the refusal must disappear."""
    original = gate._assert_mandatory_jobs_passed
    try:
        gate._assert_mandatory_jobs_passed = lambda *a, **k: None
        gate._assert_mandatory_jobs_passed(
            [], "05 Security", _resolved("05 Security"), run_id=6
        )
    finally:
        gate._assert_mandatory_jobs_passed = original
    with pytest.raises(gate.GateRefusal):
        gate._assert_mandatory_jobs_passed(
            [], "05 Security", _resolved("05 Security"), run_id=6
        )


# =============================================================================================
# 4. THE SECOND DEFECT. Two of the gate's own settings cannot both be satisfied.
# =============================================================================================

def test_pr_check_is_pull_request_only():
    """Premise of the contradiction, asserted rather than assumed."""
    doc = _workflow("01 PR Check")
    triggers = doc.get(True) or doc.get("on")
    keys = set(triggers) if isinstance(triggers, dict) else set(triggers or [])
    assert keys == {"pull_request"}, (
        "01-pr-check.yml triggers are %s; if `push` or `workflow_dispatch` was added, the "
        "contradiction below may no longer hold and should be re-derived." % sorted(keys)
    )


def test_branch_allowlist_and_pr_only_workflow_are_mutually_unsatisfiable(gate):
    """REAL evidence: `6c5660a1` is an ancestor of `main` AND has a `01 PR Check` run.

    That run reports `head_branch = 'ci/unblock-deploy-gate'`, because a `pull_request` run
    always reports the PR's own branch. `ALLOWED_BRANCHES = ('main', 'master')` therefore
    refuses it on the branch check -- before the conclusion is ever considered.

    So `01 PR Check` can never satisfy this gate, on any commit, for a structural reason. The
    two settings are mutually unsatisfiable, and the refusal an operator sees names the BRANCH
    rather than the real problem.
    """
    runs = json.loads(ANCESTOR_RUNS.read_text(encoding="utf-8-sig"))["workflow_runs"]
    pr_runs = [r for r in runs if r.get("name") == "01 PR Check"]
    assert pr_runs, "evidence capture no longer contains a 01 PR Check run for 6c5660a1"

    run = pr_runs[0]
    assert run["event"] == "pull_request"
    assert run["head_branch"] not in gate.ALLOWED_BRANCHES, (
        "the premise changed: the PR run now reports an allowed branch"
    )

    with pytest.raises(gate.GateRefusal) as refusal:
        gate._assert_run_is_admissible(run, "01 PR Check", run["head_sha"])
    message = str(refusal.value)
    assert "branch" in message.lower(), message
    # The substantive problem is that the run FAILED; the gate reports the branch instead.
    assert run["conclusion"] == "failure"
    assert "failure" not in message, (
        "if the refusal names the conclusion, the diagnostic confusion documented here is "
        "resolved and this assertion should be revisited"
    )


def test_push_triggered_security_evidence_does_satisfy_the_branch_allowlist(gate):
    """The contrast that isolates the cause: at the SAME SHA, the push-triggered
    `05 Security` run reports `main` and is admitted. So the branch allow-list is not
    wrong in general -- it is incompatible specifically with a `pull_request`-only workflow.
    """
    runs = json.loads(ANCESTOR_RUNS.read_text(encoding="utf-8-sig"))["workflow_runs"]
    push_sec = [
        r for r in runs
        if r.get("name") == "05 Security" and r.get("event") == "push"
    ]
    assert push_sec, "evidence capture no longer contains a push-triggered 05 Security run"
    run = push_sec[0]
    assert run["head_branch"] == "main"
    assert run["conclusion"] == "success"
    gate._assert_run_is_admissible(run, "05 Security", run["head_sha"])
