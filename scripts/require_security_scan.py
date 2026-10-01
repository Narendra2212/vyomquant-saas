#!/usr/bin/env python3
"""Refuse to deploy a commit whose container security scan failed.

WHY THIS EXISTS
---------------
``03-deploy.yml`` chains off ``02 Build`` through ``workflow_run``. ``05 Security`` runs on the
same push but gates nothing, so a commit whose Trivy scan found a CRITICAL vulnerability still
deployed. That is not hypothetical: ``PyJWT 2.13.0`` (CVE-2026-102268, a key-confusion signature
bypass that lets an attacker forge tokens) shipped to production while ``05 Security`` was red,
and stayed there until the pin was bumped.

WHAT IT GATES ON, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
``05-security.yml`` already runs Trivy with ``severity: CRITICAL`` and ``exit-code: 1``, so the
workflow is red only for CRITICAL findings. This script therefore inherits exactly that
threshold: CRITICAL blocks, HIGH and below report without blocking. A newly published CVE in
some transitive package should not be able to freeze an unrelated hotfix, which is what gating
on every severity would do.

TIMING
------
``02 Build`` and ``05 Security`` start from the same push and run concurrently, so the scan is
usually finished first but is not guaranteed to be. This waits rather than guessing.

A MISSING SCAN IS A FAILED GATE
-------------------------------
If no ``05 Security`` run exists for the commit, this refuses. "Nobody scanned it" is not
evidence of safety. A manual ``workflow_dispatch`` of an older commit can override with the
``allow_unscanned`` input, which is recorded in the run log.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

WORKFLOW_NAME = "05 Security"
#: How long to wait for a concurrent scan to finish before giving up.
WAIT_SECONDS = int(os.environ.get("SECURITY_GATE_WAIT_SECONDS", "900"))
POLL_SECONDS = 20


def _api(path: str, token: str) -> dict:
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def _runs_for(repo: str, sha: str, token: str) -> list:
    """Every ``05 Security`` run recorded against this exact commit."""
    data = _api("/repos/%s/actions/runs?head_sha=%s&per_page=100" % (repo, sha), token)
    return [r for r in data.get("workflow_runs", []) if r.get("name") == WORKFLOW_NAME]


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = (os.environ.get("DEPLOY_SHA") or "").strip()
    allow_unscanned = os.environ.get("ALLOW_UNSCANNED", "").lower() == "true"

    if not token or not repo or not sha:
        print("SECURITY GATE: cannot run - GITHUB_TOKEN, GITHUB_REPOSITORY and DEPLOY_SHA")
        print("are all required. Refusing rather than assuming the scan passed.")
        return 1

    print("SECURITY GATE: %s must have passed for %s" % (WORKFLOW_NAME, sha[:8]))
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
        if pending:
            print("  scan still running (%s); waiting..." % pending[0].get("status"))
        else:
            print("  no scan recorded yet for this commit; waiting...")
        time.sleep(POLL_SECONDS)

    if not runs:
        if allow_unscanned:
            print("  NO SCAN FOUND, but allow_unscanned was set for this manual run.")
            print("  Proceeding. This override is recorded here on purpose.")
            return 0
        print("  REFUSING: no %s run exists for %s." % (WORKFLOW_NAME, sha[:8]))
        print("  Nobody scanned this commit, which is not evidence that it is safe.")
        print("  Re-run 05 Security for this commit, or dispatch with allow_unscanned=true.")
        return 1

    newest = sorted(runs, key=lambda r: r.get("run_started_at") or "")[-1]
    conclusion = newest.get("conclusion")
    status = newest.get("status")

    if status != "completed":
        print("  REFUSING: the scan was still %r after %ds." % (status, WAIT_SECONDS))
        print("  A deploy that outruns its own security scan is an ungated deploy.")
        return 1

    if conclusion == "success":
        print("  PASSED: %s succeeded for this commit." % WORKFLOW_NAME)
        return 0

    if conclusion in ("cancelled", "skipped"):
        print("  REFUSING: the scan was %r, so nothing was verified." % conclusion)
        return 1

    print("  REFUSING: %s concluded %r for %s." % (WORKFLOW_NAME, conclusion, sha[:8]))
    print("  05-security.yml runs Trivy at severity CRITICAL with exit-code 1, so this means a")
    print("  CRITICAL container vulnerability is present. Fix the pin and push again.")
    print("  Run: %s" % (newest.get("html_url") or "(url unavailable)"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
