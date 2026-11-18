#!/usr/bin/env python3
"""Answer "is what is deployed what `main` says should be deployed?" for both tiers.

WHY THIS EXISTS
---------------
On 2026-11-17 the backend went stale at `b44628cd` and stayed there for a day while
`main` moved 13 commits ahead. Nothing reported it. The cause was not the deploy
failing loudly -- it was `03 Deploy`'s pre-deployment gate REFUSING, correctly, because
`01 PR Check` was red on `main` after #309 landed without re-recording a baseline
artifact. A refusal that is working as designed produces no alert, so the fleet silently
stopped shipping.

`06 Frontend Deploy` carries the SAME gate (`scripts/require_tests_passed.py`) and is in
addition `paths`-filtered on `algo22-terminal/**`, so when it declines to ship there may
be no run at all to notice -- no red X, no annotation, nothing. The frontend's version of
that outage would be even quieter than the backend's was.

So this asks the question directly, from the outside, against the running system.

WHAT "LATEST" MEANS FOR EACH TIER, AND WHY THEY DIFFER
-----------------------------------------------------
The two pipelines are independent by design, and a naive "both must equal `main` head"
check would be WRONG for the frontend: `06` only runs when `algo22-terminal/**` changes,
so after a backend-only commit the frontend legitimately serves an older SHA. Reporting
that as stale would cry wolf on every backend-only merge, and a check that cries wolf
gets ignored -- which is how the real outage went unnoticed.

  backend   expected = `main` head. `02`/`03` run on every push to main, so any gap is
            real: either a deploy is in flight or it is refusing.

  frontend  expected = the most recent commit that TOUCHES one of `06`'s trigger paths
            (`algo22-terminal/**` or the workflow file). That is precisely the newest
            commit that would have started a build. If the deployed SHA is that commit,
            the bundle is what `main` would build even though the SHA is older than
            `main` head -- "current in substance". If it is an ANCESTOR of that commit,
            a build that should have happened did not.

EXIT STATUS
-----------
0  both tiers current (or a gap younger than --grace-minutes, i.e. plausibly in flight)
1  at least one tier is stale
2  could not determine (endpoint unreachable, not a git repo, ...) -- deliberately
   distinct from "stale", because "I could not tell" must not read as "it is fine"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

#: Where each tier states the commit it was built from. Both are `{"sha", "built_at"}`.
#: `/api/release.json` and NOT `/release.json` for the backend: CloudFront proxies only
#: `/api/*`, `/ws/*`, `/health*`, `/metrics*` and `/openapi.json` to the ALB, so a bare
#: `/release.json` falls through to S3 and returns the FRONTEND's file -- which would
#: make the backend comparison silently compare the frontend against itself.
DEFAULT_BASE = "https://app.vyomquant.in"
BACKEND_RELEASE = "/api/release.json"
FRONTEND_RELEASE = "/release.json"

#: `06 Frontend Deploy`'s own `on.push.paths`. Kept in sync by
#: `tests/test_deploy02_freshness_paths_match_the_workflow.py`, which parses the workflow
#: and fails if these drift -- otherwise this check would compute "expected" from a stale
#: idea of what triggers a build, and quietly stop detecting staleness.
FRONTEND_TRIGGER_PATHS = (
    "algo22-terminal/",
    ".github/workflows/06-frontend-deploy.yml",
)


class Undetermined(Exception):
    """Raised when the answer is unknown, as opposed to known-bad."""


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:  # pragma: no cover - no git on PATH
        raise Undetermined("git is not available") from exc
    except subprocess.CalledProcessError as exc:
        raise Undetermined(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return out.stdout.strip()


def fetch_release(url: str, timeout: float) -> Tuple[str, Optional[datetime]]:
    """The `{sha, built_at}` a tier reports, or `Undetermined`."""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise Undetermined(f"{url}: {exc}") from exc

    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        raise Undetermined(f"{url}: no usable 'sha' in {payload!r}")

    built_at = None
    raw = payload.get("built_at")
    if isinstance(raw, str):
        try:
            built_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            built_at = None
    return sha, built_at


def latest_commit_touching(ref: str, paths: Tuple[str, ...]) -> str:
    """The newest commit on `ref` that changed any of `paths`.

    This is the frontend's "expected" SHA: the last commit that would have started a
    build. `git log -1 -- <paths>` is exactly the question `on.push.paths` asks.
    """
    out = _git("log", "-1", "--format=%H", ref, "--", *paths)
    if not out:
        # No commit in history touched those paths -- cannot happen for a real checkout
        # of this repo, but treat it as undetermined rather than inventing an answer.
        raise Undetermined(f"no commit on {ref} touches {paths}")
    return out


def describe(sha: str) -> str:
    try:
        return _git("log", "-1", "--format=%h %s", sha)
    except Undetermined:
        return sha[:8] + " (not in this checkout)"


def commits_between(old: str, new: str) -> Optional[int]:
    try:
        return int(_git("rev-list", "--count", f"{old}..{new}"))
    except (Undetermined, ValueError):
        return None


def is_ancestor(maybe_ancestor: str, descendant: str) -> Optional[bool]:
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", maybe_ancestor, descendant],
            capture_output=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        # exit 1 = not an ancestor; anything else = we could not tell (unknown object)
        return False if exc.returncode == 1 else None
    except FileNotFoundError:  # pragma: no cover
        return None


class Verdict:
    def __init__(self, tier: str) -> None:
        self.tier = tier
        self.state = "unknown"
        self.lines: list[str] = []

    def add(self, line: str) -> None:
        self.lines.append(line)

    def render(self) -> str:
        mark = {"current": "OK", "stale": "STALE", "in-flight": "PENDING",
                "unknown": "UNKNOWN"}[self.state]
        head = f"[{mark}] {self.tier}"
        return "\n".join([head, *(f"    {line}" for line in self.lines)])


def assess(
    tier: str,
    deployed_sha: str,
    built_at: Optional[datetime],
    expected_sha: str,
    expectation: str,
    grace: timedelta,
) -> Verdict:
    v = Verdict(tier)
    v.add(f"deployed : {describe(deployed_sha)}")
    v.add(f"expected : {describe(expected_sha)}  ({expectation})")

    if deployed_sha == expected_sha:
        v.state = "current"
        v.add("the deployed artifact was built from the expected commit")
        return v

    behind = commits_between(deployed_sha, expected_sha)
    ancestor = is_ancestor(expected_sha, deployed_sha)

    if ancestor is True:
        # Deployed is NEWER than expected. Happens if expected was computed from a
        # checkout that has not fetched yet. Not stale, but say so rather than pass mutely.
        v.state = "current"
        v.add("deployed commit is newer than expected -- local checkout may be behind")
        return v

    if behind:
        v.add(f"behind by {behind} commit(s)")

    # A gap younger than the grace window is probably a deploy in flight, not an outage.
    if built_at is not None:
        age = datetime.now(timezone.utc) - built_at
        v.add(f"built {_fmt_age(age)} ago ({built_at.isoformat()})")
        if age <= grace:
            v.state = "in-flight"
            v.add(f"within the {int(grace.total_seconds() // 60)}m grace window")
            return v

    v.state = "stale"
    return v


def _fmt_age(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins}m"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h{mins:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--ref", default="origin/main",
                    help="the ref that defines what SHOULD be deployed")
    ap.add_argument("--grace-minutes", type=int, default=30,
                    help="a gap younger than this is reported as in-flight, not stale")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--tier", choices=("backend", "frontend", "both"), default="both")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    grace = timedelta(minutes=args.grace_minutes)
    base = args.base_url.rstrip("/")
    verdicts: list[Verdict] = []
    undetermined: list[str] = []

    try:
        head = _git("rev-parse", args.ref)
    except Undetermined as exc:
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return 2

    print(f"ref {args.ref} = {describe(head)}\n")

    if args.tier in ("backend", "both"):
        try:
            sha, built = fetch_release(base + BACKEND_RELEASE, args.timeout)
            verdicts.append(assess(
                "backend", sha, built, head,
                f"{args.ref} head -- 02/03 run on every push", grace,
            ))
        except Undetermined as exc:
            undetermined.append(f"backend: {exc}")

    if args.tier in ("frontend", "both"):
        try:
            sha, built = fetch_release(base + FRONTEND_RELEASE, args.timeout)
            expected = latest_commit_touching(args.ref, FRONTEND_TRIGGER_PATHS)
            verdicts.append(assess(
                "frontend", sha, built, expected,
                "newest commit touching 06's trigger paths", grace,
            ))
        except Undetermined as exc:
            undetermined.append(f"frontend: {exc}")

    if args.json:
        print(json.dumps({
            "ref": args.ref,
            "ref_sha": head,
            "tiers": {v.tier: {"state": v.state, "detail": v.lines} for v in verdicts},
            "undetermined": undetermined,
        }, indent=2))
    else:
        for v in verdicts:
            print(v.render())
            print()
        for u in undetermined:
            print(f"[UNKNOWN] {u}")

    if undetermined:
        print("\nCould not determine the state of every tier. This is NOT a pass.",
              file=sys.stderr)
        return 2
    if any(v.state == "stale" for v in verdicts):
        stale = ", ".join(v.tier for v in verdicts if v.state == "stale")
        print(f"\nSTALE: {stale}. Check whether a deploy is REFUSING rather than failing "
              f"-- '01 PR Check' red on {args.ref} makes both pipelines decline to ship, "
              f"and 06 may not even produce a run to notice.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
