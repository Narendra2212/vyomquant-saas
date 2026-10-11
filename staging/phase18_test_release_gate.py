"""Phase 18 gate tests, including a demonstration that the Phase 17 gate is unsafe.

PROPOSAL ARTIFACT, not under `tests/`. `pytest.ini` sets `testpaths = tests`, so this is not
collected by the default lane. Run explicitly:

    .venv\\Scripts\\python.exe -m pytest staging/phase18_test_release_gate.py

Everything is an in-process fake. No network, no token, no secret, no CI dispatch, no deploy.

THREE THINGS THIS SUITE DOES THAT PHASE 17's DID NOT
  1. `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped` — executes the PHASE 17
     module and shows it ADMITS a run whose mandatory job was skipped. The defect is
     demonstrated, not asserted in prose.
  2. Job-level coverage: absent / skipped / cancelled / failed / neutral / unfinished jobs, and
     ambiguous pagination on the JOBS listing specifically.
  3. `test_the_mandatory_job_assertion_is_load_bearing` — a mutation check. It neuters the
     job assertion and proves the suite's verdict flips, so the passing cases cannot be passing
     vacuously.
"""

from __future__ import annotations

import pathlib
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import phase17_frontend_release_gate as gate17  # noqa: E402
import phase18_release_gate as gate  # noqa: E402

REPO = "Narendra2212/vyomquant-saas"
TOKEN = "fake-token-not-a-credential"
SHA = "ecd23a9e934ec58a7b1f0e5db638da8e865e8187"
OTHER_SHA = "074b772ab16cece62cbfca0f2b78497be2ae56d5"


def _run(name, conclusion="success", sha=SHA, branch="main", status="completed",
         started="2026-10-11T09:48:44Z", run_id=None):
    return {
        "id": run_id if run_id is not None else abs(hash(name)) % 100000,
        "name": name, "conclusion": conclusion, "status": status,
        "head_sha": sha, "head_branch": branch, "run_started_at": started,
    }


def _job(name, conclusion="success", status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}


def _all_runs():
    return [_run(name, run_id=1000 + i)
            for i, name in enumerate(gate.REQUIRED_WORKFLOWS)]


def _all_jobs_pass():
    """Every mandatory job of every required workflow, all green."""
    jobs = {}
    for i, (workflow, mandatory) in enumerate(gate.REQUIRED_WORKFLOWS.items()):
        jobs[1000 + i] = [_job(n) for n in mandatory]
    return jobs


@pytest.fixture
def api(monkeypatch):
    """Install fake runs + per-run jobs listings. Returns a setter."""
    def _install(runs, jobs_by_run=None, runs_total=None, jobs_total=None):
        jobs_by_run = jobs_by_run or {}

        def _fake(path, token):
            if "/jobs" in path:
                run_id = int(path.split("/runs/")[1].split("/jobs")[0])
                items = jobs_by_run.get(run_id, [])
                return {"total_count": len(items) if jobs_total is None else jobs_total,
                        "jobs": items}
            return {"total_count": len(runs) if runs_total is None else runs_total,
                    "workflow_runs": runs}

        monkeypatch.setattr(gate, "_api_get", _fake)
        monkeypatch.setattr(gate17, "_api_get", _fake)
    return _install


def _refusal(*args, **kwargs):
    with pytest.raises(gate.GateRefusal) as caught:
        gate.verify(*args, **kwargs)
    return str(caught.value)


# ─── the happy path ────────────────────────────────────────────────────────────────────

def test_admits_a_candidate_with_all_runs_and_all_mandatory_jobs_green(api):
    api(_all_runs(), _all_jobs_pass())
    admitted = gate.verify(REPO, SHA, TOKEN)
    assert len(admitted) == len(gate.REQUIRED_WORKFLOWS)
    assert sum(count for _, _, count in admitted) == sum(
        len(m) for m in gate.REQUIRED_WORKFLOWS.values()
    )


# ─── THE PHASE 17 DEFECT, DEMONSTRATED ─────────────────────────────────────────────────

def test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped(api):
    """The reason Phase 18 exists. Phase 17 checks run conclusions only.

    `01-pr-check.yml` gates most jobs on `needs: static-audit`, so an unsatisfied `needs:`
    leaves them SKIPPED while the run can still conclude success. Phase 17 admits that.
    """
    runs = [_run(name, run_id=1000 + i)
            for i, name in enumerate(gate17.REQUIRED_WORKFLOWS)]
    api(runs, {})  # no jobs listed at all — Phase 17 never asks
    admitted = gate17.verify(REPO, SHA, TOKEN)
    assert len(admitted) == len(gate17.REQUIRED_WORKFLOWS), (
        "Phase 17 was expected to admit this run; if it now refuses, the Phase 17 artifact "
        "changed and this demonstration needs re-deriving."
    )


def test_phase18_refuses_the_same_run(api):
    """The same evidence Phase 17 admits must be refused once jobs are inspected."""
    api(_all_runs(), {})  # runs green, zero jobs present
    message = _refusal(REPO, SHA, TOKEN)
    assert "mandatory job is absent" in message


# ─── job-level failure modes ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("conclusion,expected", [
    ("skipped", "a skip is not a pass"),
    ("cancelled", "nothing was verified"),
    ("failure", "concluded 'failure'"),
    ("neutral", "not a pass"),
])
def test_refuses_a_mandatory_job_that_did_not_succeed(api, conclusion, expected):
    workflow, mandatory = next(iter(gate.REQUIRED_WORKFLOWS.items()))
    jobs = _all_jobs_pass()
    jobs[1000] = [_job(mandatory[0], conclusion)] + [_job(n) for n in mandatory[1:]]
    api(_all_runs(), jobs)
    message = _refusal(REPO, SHA, TOKEN)
    assert mandatory[0] in message
    assert expected.lower() in message.lower()


def test_refuses_a_mandatory_job_still_in_progress(api):
    workflow, mandatory = next(iter(gate.REQUIRED_WORKFLOWS.items()))
    jobs = _all_jobs_pass()
    jobs[1000] = [_job(mandatory[0], None, status="in_progress")] + \
                 [_job(n) for n in mandatory[1:]]
    api(_all_runs(), jobs)
    assert "reached no verdict" in _refusal(REPO, SHA, TOKEN)


def test_refuses_when_only_some_mandatory_jobs_are_present(api):
    """A partially-reported run must not pass on the jobs that happen to be green."""
    workflow, mandatory = next(iter(gate.REQUIRED_WORKFLOWS.items()))
    jobs = _all_jobs_pass()
    jobs[1000] = [_job(mandatory[0])]  # the rest absent
    api(_all_runs(), jobs)
    message = _refusal(REPO, SHA, TOKEN)
    assert "contains no job named" in message


def test_an_advisory_job_being_red_does_not_block(api):
    """`format-check` is `continue-on-error` and nothing depends on it — it must not gate."""
    jobs = _all_jobs_pass()
    jobs[1000] = jobs[1000] + [_job("format-check", "failure")]
    api(_all_runs(), jobs)
    assert gate.verify(REPO, SHA, TOKEN)
    assert "format-check" in gate.ADVISORY_JOBS


# ─── jobs-listing pagination ───────────────────────────────────────────────────────────

def test_fails_closed_on_ambiguous_jobs_pagination(api):
    api(_all_runs(), _all_jobs_pass(), jobs_total=500)
    assert "Pagination is ambiguous for jobs" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_when_the_jobs_page_is_full(api):
    jobs = _all_jobs_pass()
    jobs[1000] = [_job("filler-%d" % i) for i in range(gate._PER_PAGE)]
    api(_all_runs(), jobs, jobs_total=gate._PER_PAGE)
    assert "fills the page" in _refusal(REPO, SHA, TOKEN)


def test_fails_closed_on_http_error_listing_jobs(api, monkeypatch):
    runs = _all_runs()

    def _fake(path, token):
        if "/jobs" in path:
            raise urllib.error.HTTPError(path, 403, "forbidden", {}, None)
        return {"total_count": len(runs), "workflow_runs": runs}

    monkeypatch.setattr(gate, "_api_get", _fake)
    message = _refusal(REPO, SHA, TOKEN)
    assert "403" in message and "jobs" in message


# ─── run-level behaviour carried over from Phase 17, re-proved here ────────────────────

def test_refuses_the_actual_ecd23a9e_incident(api):
    api([_run("02 Build"), _run("05 Security", "failure", run_id=38129699454)],
        _all_jobs_pass())
    message = _refusal(REPO, SHA, TOKEN)
    assert "05 Security" in message and "failure" in message


def test_refuses_when_a_required_workflow_never_ran(api):
    """`01 PR Check` is pull_request-only, so a direct push has no run for it at all."""
    api([_run("05 Security", run_id=1000)], {1000: [_job(n) for n in
                                                    gate.REQUIRED_WORKFLOWS["05 Security"]]})
    message = _refusal(REPO, SHA, TOKEN)
    assert "01 PR Check" in message
    assert "not evidence" in message


def test_reports_every_blocker_in_one_pass(api):
    """A release gate must not reveal one blocker per CI cycle.

    Here BOTH required workflows are unusable: `05 Security` is green at run level but has a
    skipped mandatory job, and `01 PR Check` never ran. One refusal must name both.
    """
    jobs = {1000: [_job("sast-scan", "skipped")] +
                  [_job(n) for n in gate.REQUIRED_WORKFLOWS["05 Security"][1:]]}
    api([_run("05 Security", run_id=1000)], jobs)
    message = _refusal(REPO, SHA, TOKEN)
    assert "sast-scan" in message and "skip is not a pass" in message
    assert "01 PR Check" in message
    assert message.startswith("2 of 2 required workflow(s)")


def test_a_newer_failure_overrides_an_older_success(api):
    api(_all_runs() + [_run("05 Security", "failure",
                            started="2026-10-11T10:38:58Z", run_id=38132697285)],
        _all_jobs_pass())
    assert "failure" in _refusal(REPO, SHA, TOKEN)


def test_refuses_evidence_from_another_sha(api):
    api([_run(n, sha=OTHER_SHA, run_id=1000 + i)
         for i, n in enumerate(gate.REQUIRED_WORKFLOWS)], _all_jobs_pass())
    assert "another commit" in _refusal(REPO, SHA, TOKEN)


def test_refuses_a_branch_outside_the_allow_list(api):
    api([_run(n, branch="feature/x", run_id=1000 + i)
         for i, n in enumerate(gate.REQUIRED_WORKFLOWS)], _all_jobs_pass())
    assert "not in the allowed set" in _refusal(REPO, SHA, TOKEN)


@pytest.mark.parametrize("bad", ["", "ecd23a9e", "HEAD", "main",
                                 "ECD23A9E934EC58A7B1F0E5DB638DA8E865E8187"])
def test_refuses_a_malformed_sha(api, bad):
    api(_all_runs(), _all_jobs_pass())
    with pytest.raises(gate.GateRefusal):
        gate.verify(REPO, bad, TOKEN)


def test_refuses_an_empty_policy(api, monkeypatch):
    api(_all_runs(), _all_jobs_pass())
    monkeypatch.setattr(gate, "REQUIRED_WORKFLOWS", {})
    assert "empty policy is a misconfiguration" in _refusal(REPO, SHA, TOKEN)


def test_there_is_no_override_escape_hatch():
    source = pathlib.Path(gate.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # exclude the module docstring's prose
    for forbidden in ("ALLOW_UNSCANNED", "ALLOW_UNVERIFIED", "SKIP_GATE", "FORCE_DEPLOY"):
        assert forbidden not in body, "an override escape hatch appeared: %s" % forbidden


# ─── non-vacuity: the assertions are load-bearing ──────────────────────────────────────

def test_the_mandatory_job_assertion_is_load_bearing(api, monkeypatch):
    """Mutation check. Neuter the job assertion; the refusal must disappear.

    If this test fails, the job-level cases above are passing for some reason other than the
    assertion under test, and their green is meaningless.
    """
    api(_all_runs(), {})  # mandatory jobs absent → must refuse normally
    with pytest.raises(gate.GateRefusal):
        gate.verify(REPO, SHA, TOKEN)

    monkeypatch.setattr(gate, "_assert_mandatory_jobs_passed",
                        lambda jobs, wf, mandatory, run_id: None)
    assert gate.verify(REPO, SHA, TOKEN), (
        "with the job assertion neutered the gate still refused, so the job-level tests are "
        "not actually exercising it"
    )


def test_the_run_conclusion_assertion_is_load_bearing(api, monkeypatch):
    api([_run("02 Build"), _run("05 Security", "failure")], _all_jobs_pass())
    with pytest.raises(gate.GateRefusal):
        gate.verify(REPO, SHA, TOKEN)

    monkeypatch.setattr(gate, "_assert_run_is_admissible",
                        lambda run, wf, sha: None)
    with pytest.raises(gate.GateRefusal) as caught:
        gate.verify(REPO, SHA, TOKEN)
    message = str(caught.value)
    assert "concluded 'failure'" not in message, (
        "the run-conclusion refusal survived its own neutering, so "
        "test_refuses_the_actual_ecd23a9e_incident is not exercising that assertion"
    )
