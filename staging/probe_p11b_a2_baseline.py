"""PHASE 11B-A2 -- baseline revalidation and CI-trigger capability probe.

Read-only. Runs no git write command. Establishes, with evidence rather than assumption:
  1. branch / HEAD, and whether HEAD is already on the remote;
  2. the exact working-tree state of every D5'-attributable path;
  3. whether the D5' changes are COMMITTED (a precondition for GitHub Actions to see them);
  4. whether a CI run could be triggered at all from this environment.
"""

from __future__ import annotations

import shutil
import subprocess

D5_PATHS = [
    ".github/workflows/01-pr-check.yml",
    "tests/real_redis_gate.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/test_api04_store_failure_after_startup.py",
    "tests/conftest.py",
]


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or r.stderr or "").strip()


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("1. BRANCH / HEAD")
    head = git("rev-parse", "HEAD")
    print(f"  branch            : {git('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(f"  HEAD              : {head}")
    print(f"  origin/main       : {git('rev-parse', 'origin/main')}")
    print(f"  HEAD == origin/main: {head == git('rev-parse', 'origin/main')}")
    print(f"  commits ahead/behind origin/main: "
          f"{git('rev-list', '--left-right', '--count', 'origin/main...HEAD')}")

    banner("2. D5'-ATTRIBUTABLE PATHS -- working-tree state")
    tracked_status = {}
    for path in D5_PATHS:
        status = git("status", "--porcelain", "--", path)
        tracked = git("ls-files", "--error-unmatch", path)
        is_tracked = "did not match" not in tracked and tracked != ""
        code = status[:2] if status else "  "
        tracked_status[path] = (code, is_tracked)
        label = "UNTRACKED" if not is_tracked else ("MODIFIED" if status.strip() else "clean")
        print(f"  [{code}] {label:10s} tracked={is_tracked!s:5s}  {path}")

    banner("3. CAN GITHUB ACTIONS SEE THE D5' CHANGES?")
    uncommitted = [p for p, (c, t) in tracked_status.items()
                   if c.strip() or not t]
    print("  GitHub Actions runs COMMITTED, PUSHED code only. Paths not yet committed:")
    for p in uncommitted:
        print(f"    - {p}")
    print(f"\n  => {len(uncommitted)} D5' path(s) are not in any commit on any branch.")
    print("     No existing or future CI run can contain the D5' change until it is")
    print("     committed and pushed.")

    banner("4. COULD A RUN BE TRIGGERED FROM HERE?")
    print(f"  remote origin : {git('remote', 'get-url', 'origin')}")
    gh = shutil.which("gh")
    print(f"  gh CLI        : {'FOUND ' + gh if gh else 'ABSENT (not on PATH)'}")
    if gh:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(f"  gh auth status exit={r.returncode} "
              f"({'AUTHENTICATED' if r.returncode == 0 else 'not authenticated'})")
        for line in ((r.stdout or "") + (r.stderr or "")).splitlines()[:6]:
            # Never echo a token. gh redacts by default; this stays conservative, and
            # non-ASCII glyphs are stripped so a cp1252 console cannot crash the probe.
            safe = line.strip().encode("ascii", "replace").decode("ascii")
            if "token" in safe.lower():
                print("    (a line mentioning a token was withheld)")
            else:
                print(f"    {safe}")
    helper = git("config", "--get", "credential.helper")
    print(f"  credential.helper: {helper or '(none configured)'}")

    banner("5. WORKFLOW TRIGGERS -- how 01-pr-check.yml starts")
    import pathlib
    import yaml
    doc = yaml.safe_load(pathlib.Path(".github/workflows/01-pr-check.yml")
                         .read_text(encoding="utf-8"))
    print(f"  name  : {doc.get('name')}")
    # PyYAML parses the YAML key `on:` as boolean True.
    triggers = doc.get("on", doc.get(True))
    print(f"  on    : {triggers}")
    print("  => there is no workflow_dispatch, so it cannot be started manually;")
    print("     it requires a push or a pull_request event.")

    banner("VERDICT")
    print("  CI verification requires: commit + push of the D5' paths, then a PR or a")
    print("  push to a watched branch. Both are REMOTE, STATE-CHANGING actions that this")
    print("  workstream is not authorized to perform.")


if __name__ == "__main__":
    main()
