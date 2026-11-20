#!/usr/bin/env python3
"""Refuse to deploy a commit whose test workflow did not pass (CI-01).

``03-deploy.yml`` chains off ``02 Build`` through ``workflow_run``. ``01 PR Check`` - the backend
unit lane, the PostgreSQL lane, provisioning/isolation, the paper-mode lane, the Docker build
and the frontend lane - runs on the same commit but gated nothing, so a commit whose tests were
red (or never ran) still deployed. This is the same gate ``require_security_scan.py`` applies
to ``05 Security``, pointed at the test workflow: the newest ``01 PR Check`` run for the exact
deploy SHA must have concluded ``success``.

* A MISSING run is a FAILED gate: "nobody tested it" is not evidence of safety.
* A run that is still going is waited for (``TEST_GATE_WAIT_SECONDS``, default 90 minutes: the
  backend lane's own budget is 90).
* ``cancelled`` / ``skipped`` / ``failure`` / ``timed_out`` all refuse.
* A manual ``workflow_dispatch`` of an older commit may set ``allow_untested``; the override is
  printed so it is recorded in the run log.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

WORKFLOW_NAME = os.environ.get("GATE_WORKFLOW_NAME", "01 PR Check")
WAIT_SECONDS = int(os.environ.get("TEST_GATE_WAIT_SECONDS", "5400"))
POLL_SECONDS = 30


def _api(path: str, token: str) -> dict:
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def _runs_for(repo: str, sha: str, token: str) -> list:
    """Every run of the gated workflow recorded against this exact commit."""
    data = _api("/repos/%s/actions/runs?head_sha=%s&per_page=100" % (repo, sha), token)
    return [r for r in data.get("workflow_runs", []) if r.get("name") == WORKFLOW_NAME]


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = (os.environ.get("DEPLOY_SHA") or "").strip()
    allow_untested = os.environ.get("ALLOW_UNTESTED", "").lower() == "true"

    if not token or not repo or not sha:
        print("TEST GATE: cannot run - GITHUB_TOKEN, GITHUB_REPOSITORY and DEPLOY_SHA are all")
        print("required. Refusing rather than assuming the tests passed.")
        return 1

    print("TEST GATE: %s must have passed for %s" % (WORKFLOW_NAME, sha[:8]))
    deadline = time.time() + WAIT_SECONDS
    runs: list = []
    while True:
        try:
            runs = _runs_for(repo, sha, token)
        except urllib.error.HTTPError as exc:
            print("  GitHub API returned %s; refusing rather than guessing." % exc.code)
            return 1
        pending = [r for r in runs if r.get("status") != "completed"]
        if runs and not pending:
            break
        if time.time() >= deadline:
            break
        print("  tests still running (%s); waiting..." % pending[0].get("status") if pending
              else "  no test run recorded yet for this commit; waiting...")
        time.sleep(POLL_SECONDS)

    if not runs:
        if allow_untested:
            print("  NO TEST RUN FOUND, but allow_untested was set for this manual run.")
            print("  Proceeding. This override is recorded here on purpose.")
            return 0
        print("  REFUSING: no %s run exists for %s." % (WORKFLOW_NAME, sha[:8]))
        print("  Nobody tested this commit, which is not evidence that it works.")
        return 1

    newest = sorted(runs, key=lambda r: r.get("run_started_at") or "")[-1]
    if newest.get("status") != "completed":
        print("  REFUSING: the tests were still %r after %ds." % (newest.get("status"), WAIT_SECONDS))
        return 1
    if newest.get("conclusion") == "success":
        print("  PASSED: %s succeeded for this commit." % WORKFLOW_NAME)
        return 0
    print("  REFUSING: %s concluded %r for %s." % (WORKFLOW_NAME, newest.get("conclusion"), sha[:8]))
    print("  Run: %s" % (newest.get("html_url") or "(url unavailable)"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
