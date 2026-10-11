"""PROPOSAL ARTIFACT for D-P15-4 — same-SHA release gate for `06 Frontend Deploy`.

NOT INSTALLED. This file lives under `staging/` on purpose. D-P15-4 is a *request*, not a
grant (`staging/PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md:313-316`); no implementation
authorization exists, so nothing under `.github/` or `scripts/` was modified. On a grant this
module is copied to `scripts/require_release_evidence.py` and wired per the report's §A2.

WHAT IT IS. A generalisation of the working backend gate `scripts/require_security_scan.py`
(verified closed and functioning, A23E17 §12) from one required workflow to N, with the
selection and fail-closed policy tightened for the failure modes the historical incident and
the Phase 17 brief enumerate.

THE POLICY, STATED SO IT CAN BE DISAGREED WITH
----------------------------------------------
For a candidate SHA, every workflow named in REQUIRED_WORKFLOWS must have at least one run
recorded against that exact SHA, and the NEWEST such run (by run_started_at) must have
concluded `success`. Anything else refuses.

"Newest wins" is the load-bearing difference from a naive `any(success)`. A commit can carry
an older green run and a newer red one — a re-run after a dependency pin changed, say. Taking
`any(success)` would release on stale evidence that a later run contradicted. The backend gate
already does this correctly (`require_security_scan.py:106` sorts and takes `[-1]`); this
module keeps that and adds an explicit test for it, because it is the subtlest rule here.

FAIL CLOSED, in every one of these cases:
  * a required env var is missing or the SHA is malformed
  * the GitHub API errors, rate-limits, or returns unparseable data
  * no run exists for the SHA for any required workflow
  * the newest run concluded failure / cancelled / skipped / timed_out / stale / None
  * the newest run is still in progress (status != "completed")
  * a run's head_sha does not match the candidate exactly (defence against API filtering drift)
  * a branch allow-list is configured and the run's head_branch is outside it
  * pagination is ambiguous: the result set fills the page and more pages may exist

NO OVERRIDE. `require_security_scan.py` offers `ALLOW_UNSCANNED`; this module deliberately
does not, per the brief's "never allow a manually dispatched run to bypass mandatory checks".
A `workflow_dispatch` of the deploy must still satisfy the same evidence requirement.

THE TRAP THIS MODULE CANNOT FIX ON ITS OWN — read before wiring
---------------------------------------------------------------
If the deploy workflow is converted to `on: workflow_run`, the checkout is the dangerous part,
not the gate. A `workflow_run`-triggered run uses the DEFAULT BRANCH, and `actions/checkout`
in that context defaults to the default branch rather than the commit that triggered the
upstream run. So a gate that validates SHA X can sit in front of a build of `main`'s tip,
publishing Y while attesting X — strictly worse than no gate, because it looks verified.

The checkout MUST therefore pin the ref explicitly:

    - uses: actions/checkout@v4
      with:
        ref: ${{ github.event.workflow_run.head_sha }}

and the gate must be passed that same SHA, so the SHA validated, the SHA built and the SHA
published are provably one value. Sources:
  * actions/checkout defaults to the triggering event's ref only when checking out the repo
    that triggered the workflow, "Otherwise, uses the default branch" —
    https://github.com/actions/checkout/discussions/1637
  * workflow_run-triggered runs use the repository's default branch —
    https://stackoverflow.com/questions/63343937/how-to-use-the-new-event-workflow-run-of-gtihub-action-added-recently/
  * a SHA reachable from several branches can persist as head_sha, which is why head_branch is
    checked rather than assumed —
    https://stackoverflow.com/questions/79941021/github-actions-workflow-run-trigger-keeps-firing-with-old-commit-sha-even-after
(Content from these sources was read and paraphrased; no text was copied verbatim.)

Requires `actions: read` on the job, exactly as `03-deploy.yml:33-38` documents for the
backend gate.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

#: Every workflow whose success is mandatory for a frontend release. Names must match the
#: `name:` field of the workflow, which is what the API reports — not the filename.
REQUIRED_WORKFLOWS = ("05 Security", "01 PR Check")

#: Branches a release candidate may come from. Empty tuple disables the check.
ALLOWED_BRANCHES = ("main", "master")

#: Conclusions that are NOT success. Enumerated rather than inferred so a new GitHub
#: conclusion value fails closed instead of silently passing an `!= "failure"` test.
_REFUSING_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "skipped", "timed_out", "stale", "action_required", "neutral"}
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PER_PAGE = 100
_API = "https://api.github.com"


class GateRefusal(Exception):
    """Raised for every refusal. Carrying one type keeps the caller fail-closed by default."""


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


def _runs_for_sha(repo: str, sha: str, token: str) -> list:
    """Every workflow run recorded against this exact SHA.

    Pagination is treated as a refusal condition rather than silently truncated: if the first
    page is full we cannot prove we have seen the newest run for a workflow, and guessing is
    the failure mode this gate exists to prevent.
    """
    path = "/repos/%s/actions/runs?head_sha=%s&per_page=%d" % (repo, sha, _PER_PAGE)
    try:
        payload = _api_get(path, token)
    except urllib.error.HTTPError as exc:
        raise GateRefusal(
            "GitHub API returned HTTP %s listing runs for %s. Refusing rather than "
            "assuming the required checks passed." % (exc.code, sha[:8])
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GateRefusal(
            "GitHub API unreachable (%s). Refusing rather than guessing." % exc
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise GateRefusal(
            "GitHub API returned unparseable JSON (%s). Refusing." % exc
        ) from exc

    if not isinstance(payload, dict):
        raise GateRefusal("GitHub API returned a non-object payload. Refusing.")

    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise GateRefusal(
            "GitHub API response has no usable 'workflow_runs' array. Refusing."
        )

    total = payload.get("total_count")
    if isinstance(total, int) and total > len(runs):
        raise GateRefusal(
            "Pagination is ambiguous: the API reports %d runs for %s but returned %d. "
            "The newest run for a required workflow may be unseen, so this cannot be "
            "decided. Refusing." % (total, sha[:8], len(runs))
        )
    if len(runs) >= _PER_PAGE:
        raise GateRefusal(
            "Pagination is ambiguous: %d runs returned for %s fills the page, so a newer "
            "run may exist on a page that was not fetched. Refusing." % (len(runs), sha[:8])
        )
    return runs


def _newest_run_for(runs: list, workflow_name: str) -> dict:
    """The newest run of one workflow for the candidate, or a refusal.

    `run_started_at` is the ordering key, matching the backend gate. A run missing that key
    sorts to the beginning rather than crashing, and if the SELECTED run lacks required
    metadata the caller refuses.
    """
    matching = [r for r in runs if isinstance(r, dict) and r.get("name") == workflow_name]
    if not matching:
        raise GateRefusal(
            "No %r run exists for this commit. Nobody ran it, which is not evidence that "
            "the commit is releasable." % workflow_name
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

    if ALLOWED_BRANCHES:
        branch = run.get("head_branch")
        if branch not in ALLOWED_BRANCHES:
            raise GateRefusal(
                "The newest %r run is on branch %r, which is not in the allowed set %s. "
                "Refusing." % (workflow_name, branch, list(ALLOWED_BRANCHES))
            )

    status = run.get("status")
    if status != "completed":
        raise GateRefusal(
            "The newest %r run has status %r, so no verdict exists yet. Refusing rather "
            "than releasing on an unfinished check." % (workflow_name, status)
        )

    conclusion = run.get("conclusion")
    if conclusion is None:
        raise GateRefusal(
            "The newest %r run reports no conclusion. Refusing." % workflow_name
        )
    if conclusion != "success":
        detail = (
            " Nothing was verified." if conclusion in ("cancelled", "skipped") else ""
        )
        raise GateRefusal(
            "%r concluded %r for this commit.%s Refusing."
            % (workflow_name, conclusion, detail)
        )


def verify(repo: str, sha: str, token: str) -> list:
    """Return the admitted run per required workflow, or raise GateRefusal.

    Pure with respect to the environment: everything it needs is an argument, which is what
    makes the test suite able to drive every failure mode deterministically.
    """
    if not repo:
        raise GateRefusal("Repository is empty. Refusing.")
    if not token:
        raise GateRefusal("No API token supplied. Refusing rather than running unauthenticated.")
    if not sha:
        raise GateRefusal("Candidate SHA is empty. Refusing.")
    if not _SHA_RE.match(sha):
        raise GateRefusal(
            "Candidate SHA %r is not a full 40-character lowercase hex SHA. An abbreviated "
            "or malformed SHA cannot be matched exactly, so this refuses rather than "
            "matching loosely." % sha
        )
    if not REQUIRED_WORKFLOWS:
        raise GateRefusal(
            "REQUIRED_WORKFLOWS is empty, so this gate would admit everything. Refusing: an "
            "empty policy is a misconfiguration, not a pass."
        )

    runs = _runs_for_sha(repo, sha, token)

    admitted = []
    for workflow_name in REQUIRED_WORKFLOWS:
        run = _newest_run_for(runs, workflow_name)
        _assert_run_is_admissible(run, workflow_name, sha)
        admitted.append(run)
    return admitted


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = (os.environ.get("RELEASE_SHA") or "").strip()

    print("RELEASE GATE: %s must all have passed for %s"
          % (", ".join(repr(w) for w in REQUIRED_WORKFLOWS), sha[:8] or "<unset>"))

    try:
        admitted = verify(repo, sha, token)
    except GateRefusal as refusal:
        print("  REFUSING: %s" % refusal)
        print("  No frontend asset was published. Re-run the required workflows for this")
        print("  commit, or release a commit that has the evidence.")
        return 1

    for run in admitted:
        print("  PASSED: %r succeeded for %s (run %s)"
              % (run.get("name"), sha[:8], run.get("id")))
    print("  All %d required workflow(s) passed for this exact commit." % len(admitted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
