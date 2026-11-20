"""PROPOSAL ARTIFACT — Phase 18 release gate. SUPERSEDES `staging/phase17_frontend_release_gate.py`.

NOT INSTALLED. Still no grant: `D-P15-4` / `D-P15-3` / `D-P15-1` remain requests, and PHASE19
re-measured "`GRANTED` occurrences anywhere in the ledger: 0". Nothing under `.github/` or
`scripts/` was modified. Phase 17's artifact is left byte-identical on purpose so the two can be
diffed.

WHY A PHASE 18 VERSION EXISTS — a real defect in the Phase 17 gate
------------------------------------------------------------------
Phase 17's gate checks the **workflow-run** conclusion only. That is not sufficient, and the
repository's own workflows are shaped exactly the way that makes it unsafe:

  * `01-pr-check.yml` carries `continue-on-error: true` at :96, :116, :120, and its own comments
    at :22-57 describe the hazard ("`format-check` ... `continue-on-error: true`. Nothing depends
    on it." / ":57 `continue-on-error`, and the ratchet is back").
  * Most of its jobs are gated on `needs: static-audit` (:126, :286, :384, :568, :673), so a
    `needs:` that is not satisfied leaves them **skipped** rather than failed.
  * `05-security.yml` has three jobs (`sast-scan` :16, `container-security` :47, `iac-scan` :108)
    plus a summary job with `if: always()` :158.

A run can therefore conclude `success` while a mandatory job inside it was **skipped** or was
soft-failed. The Phase 17 gate admits such a run. `phase18_test_release_gate.py` contains
`test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`, which demonstrates the defect
against the Phase 17 module itself rather than asserting it in prose.

THE PHASE 18 POLICY — run-level AND job-level, both required
------------------------------------------------------------
For a candidate SHA, for every workflow in REQUIRED_WORKFLOWS:
  1. at least one run exists for that exact SHA;
  2. the NEWEST such run (by run_started_at) concluded `success`;
  3. every job named in MANDATORY_JOBS for that workflow exists in that run AND concluded
     `success` — `skipped`, `cancelled`, `failure`, `neutral` and absent are all refusals.

Requiring an explicit `success` rather than "not failure" is deliberate: it is robust to however
`continue-on-error` happens to surface in the API, and to any conclusion value GitHub adds later.

Fails closed additionally on: malformed/missing SHA, missing token or repo, HTTP error,
unreachable API, unparseable or non-object payload, absent arrays, `head_sha` mismatch, branch
outside the allow-list, unfinished status, ambiguous pagination on **either** the runs listing or
the jobs listing, and an empty policy. No override exists.

Carried forward from Phase 17 and still load-bearing: the `workflow_run` checkout trap. A
`workflow_run`-triggered run uses the default branch and `actions/checkout` defaults to it, so the
checkout MUST pin `ref: ${{ github.event.workflow_run.head_sha }}` or the gate validates one commit
while the job publishes another. See `staging/PHASE17_D_P15_4_RELEASE_GATE_AND_PARALLEL_CLOSURE.md`
§A2.3 for sources.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

#: Workflows whose success is mandatory, and the jobs within each that must individually have
#: concluded `success`. An empty job tuple means "run-level only" and is recorded explicitly
#: rather than left implicit. Job names must match the API's `name`, i.e. the `name:` field if
#: present, otherwise the job id.
REQUIRED_WORKFLOWS: dict = {
    "05 Security": ("sast-scan", "container-security", "iac-scan"),
    "01 PR Check": ("static-audit", "unit-tests", "paper-mode-tests", "database-tests",
                    "docker-check", "frontend-tests"),
}

#: Deliberately excluded from MANDATORY_JOBS, with the reason, so the omission is reviewable:
#:   * `format-check` (01 PR Check :89)  -- `continue-on-error: true`, nothing depends on it,
#:     and the workflow's own comments describe it as not blocking yet.
#:   * `provisioning-and-isolation` (:565) -- provisions a PostgreSQL service; whether it is
#:     release-mandatory is an owner decision, not this module's to assume.
#:   * `05 Security`'s `if: always()` summary job (:158) -- a reporter, not a verdict.
ADVISORY_JOBS = ("format-check", "provisioning-and-isolation")

ALLOWED_BRANCHES = ("main", "master")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PER_PAGE = 100
_API = "https://api.github.com"


class GateRefusal(Exception):
    """Every refusal. One type keeps callers fail-closed by default."""


def _api_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        _API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer %s" % token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aerora-release-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
        return json.loads(response.read().decode("utf-8"))


def _get_list(path: str, token: str, key: str, what: str) -> list:
    """A paginated-but-single-page listing, or a refusal. Ambiguity is never resolved by guessing."""
    try:
        payload = _api_get(path, token)
    except urllib.error.HTTPError as exc:
        raise GateRefusal(
            "GitHub API returned HTTP %s listing %s. Refusing rather than assuming the "
            "required checks passed." % (exc.code, what)
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GateRefusal(
            "GitHub API unreachable listing %s (%s). Refusing rather than guessing."
            % (what, exc)
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise GateRefusal(
            "GitHub API returned unparseable JSON listing %s (%s). Refusing." % (what, exc)
        ) from exc

    if not isinstance(payload, dict):
        raise GateRefusal("GitHub API returned a non-object payload for %s. Refusing." % what)
    items = payload.get(key)
    if not isinstance(items, list):
        raise GateRefusal(
            "GitHub API response for %s has no usable %r array. Refusing." % (what, key)
        )

    total = payload.get("total_count")
    if isinstance(total, int) and total > len(items):
        raise GateRefusal(
            "Pagination is ambiguous for %s: the API reports %d but returned %d, so a "
            "relevant entry may be unseen. Refusing." % (what, total, len(items))
        )
    if len(items) >= _PER_PAGE:
        raise GateRefusal(
            "Pagination is ambiguous for %s: %d entries fills the page, so more may exist "
            "on a page that was not fetched. Refusing." % (what, len(items))
        )
    return items


def _runs_for_sha(repo: str, sha: str, token: str) -> list:
    return _get_list(
        "/repos/%s/actions/runs?head_sha=%s&per_page=%d" % (repo, sha, _PER_PAGE),
        token, "workflow_runs", "workflow runs for %s" % sha[:8],
    )


def _jobs_for_run(repo: str, run_id, token: str) -> list:
    return _get_list(
        "/repos/%s/actions/runs/%s/jobs?per_page=%d" % (repo, run_id, _PER_PAGE),
        token, "jobs", "jobs for run %s" % run_id,
    )


def _newest_run_for(runs: list, workflow_name: str) -> dict:
    matching = [r for r in runs if isinstance(r, dict) and r.get("name") == workflow_name]
    if not matching:
        raise GateRefusal(
            "No %r run exists for this commit. Nobody ran it, which is not evidence that the "
            "commit is releasable." % workflow_name
        )
    return sorted(matching, key=lambda r: r.get("run_started_at") or "")[-1]


def _assert_run_is_admissible(run: dict, workflow_name: str, sha: str) -> None:
    head_sha = run.get("head_sha")
    if not head_sha:
        raise GateRefusal(
            "The newest %r run carries no head_sha, so it cannot be attributed to this "
            "commit. Refusing." % workflow_name
        )
    if head_sha != sha:
        raise GateRefusal(
            "The newest %r run is recorded against %s, not the candidate %s. Evidence from "
            "another commit is not evidence for this one. Refusing."
            % (workflow_name, str(head_sha)[:8], sha[:8])
        )
    if ALLOWED_BRANCHES and run.get("head_branch") not in ALLOWED_BRANCHES:
        raise GateRefusal(
            "The newest %r run is on branch %r, which is not in the allowed set %s. Refusing."
            % (workflow_name, run.get("head_branch"), list(ALLOWED_BRANCHES))
        )
    if run.get("status") != "completed":
        raise GateRefusal(
            "The newest %r run has status %r, so no verdict exists yet. Refusing rather than "
            "releasing on an unfinished check." % (workflow_name, run.get("status"))
        )
    conclusion = run.get("conclusion")
    if conclusion is None:
        raise GateRefusal("The newest %r run reports no conclusion. Refusing." % workflow_name)
    if conclusion != "success":
        extra = " Nothing was verified." if conclusion in ("cancelled", "skipped") else ""
        raise GateRefusal(
            "%r concluded %r for this commit.%s Refusing." % (workflow_name, conclusion, extra)
        )


def _assert_mandatory_jobs_passed(jobs: list, workflow_name: str,
                                  mandatory: tuple, run_id) -> None:
    """THE PHASE 18 ADDITION. A green run with a skipped mandatory job is not evidence."""
    by_name = {}
    for job in jobs:
        if isinstance(job, dict) and job.get("name"):
            by_name[job["name"]] = job

    for job_name in mandatory:
        job = by_name.get(job_name)
        if job is None:
            raise GateRefusal(
                "Run %s of %r contains no job named %r. The run concluded success, but the "
                "mandatory job is absent, so the check it represents did not happen. "
                "Refusing. (jobs present: %s)"
                % (run_id, workflow_name, job_name, sorted(by_name) or "none")
            )
        if job.get("status") != "completed":
            raise GateRefusal(
                "Mandatory job %r in %r has status %r, so it reached no verdict. Refusing."
                % (job_name, workflow_name, job.get("status"))
            )
        conclusion = job.get("conclusion")
        if conclusion != "success":
            detail = {
                "skipped": " It was SKIPPED -- a skip is not a pass.",
                "cancelled": " It was cancelled, so nothing was verified.",
                "failure": "",
                "neutral": " A neutral conclusion is not a pass.",
            }.get(conclusion, "")
            raise GateRefusal(
                "Mandatory job %r in %r concluded %r.%s Refusing."
                % (job_name, workflow_name, conclusion, detail)
            )


def verify(repo: str, sha: str, token: str) -> list:
    """Every admitted (workflow, run, mandatory-job-count), or raise GateRefusal."""
    if not repo:
        raise GateRefusal("Repository is empty. Refusing.")
    if not token:
        raise GateRefusal("No API token supplied. Refusing rather than running unauthenticated.")
    if not sha:
        raise GateRefusal("Candidate SHA is empty. Refusing.")
    if not _SHA_RE.match(sha):
        raise GateRefusal(
            "Candidate SHA %r is not a full 40-character lowercase hex SHA. An abbreviated or "
            "malformed SHA cannot be matched exactly, so this refuses rather than matching "
            "loosely." % sha
        )
    if not REQUIRED_WORKFLOWS:
        raise GateRefusal(
            "REQUIRED_WORKFLOWS is empty, so this gate would admit everything. Refusing: an "
            "empty policy is a misconfiguration, not a pass."
        )

    runs = _runs_for_sha(repo, sha, token)

    # EVERY problem is collected, not just the first. A release gate that reports one blocker
    # per attempt costs an operator a full CI cycle per finding; reporting all of them means a
    # single pass names everything that must be fixed. Infrastructure-level refusals (API,
    # pagination) still raise immediately from the helpers, because in that state the remaining
    # checks cannot be evaluated at all and continuing would invent evidence.
    admitted = []
    problems = []
    for workflow_name, mandatory in REQUIRED_WORKFLOWS.items():
        try:
            run = _newest_run_for(runs, workflow_name)
            _assert_run_is_admissible(run, workflow_name, sha)
        except GateRefusal as refusal:
            problems.append(str(refusal))
            continue
        if mandatory:
            jobs = _jobs_for_run(repo, run.get("id"), token)
            try:
                _assert_mandatory_jobs_passed(jobs, workflow_name, mandatory, run.get("id"))
            except GateRefusal as refusal:
                problems.append(str(refusal))
                continue
        admitted.append((workflow_name, run.get("id"), len(mandatory)))

    if problems:
        raise GateRefusal(
            "%d of %d required workflow(s) did not provide admissible evidence for %s:\n  - %s"
            % (len(problems), len(REQUIRED_WORKFLOWS), sha[:8], "\n  - ".join(problems))
        )
    return admitted


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = (os.environ.get("RELEASE_SHA") or "").strip()

    print("RELEASE GATE: %d workflow(s) and their mandatory jobs must have passed for %s"
          % (len(REQUIRED_WORKFLOWS), sha[:8] or "<unset>"))
    try:
        admitted = verify(repo, sha, token)
    except GateRefusal as refusal:
        print("  REFUSING: %s" % refusal)
        print("  Nothing was published. Re-run the required workflows for this commit, or")
        print("  release a commit that has the evidence.")
        return 1

    for workflow_name, run_id, job_count in admitted:
        print("  PASSED: %r run %s, including %d mandatory job(s)"
              % (workflow_name, run_id, job_count))
    print("  All required evidence is present for this exact commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
