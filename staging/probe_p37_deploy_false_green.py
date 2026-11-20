"""Phase 37 — isolated reproduction of the `scripts/deploy.sh` false-green defect.

WHY THIS EXISTS
---------------
Phase 32 reported that `scripts/deploy.sh` exits 0 and prints its success banner even when
every health check fails. Phase 37 section 4 requires that behaviour be reproduced "safely
with stubbed `kubectl`, `curl`, `sleep` ... in a throwaway directory" and explicitly forbids
targeting a real cluster.

No grant exists for `scripts/deploy.sh`, so this probe does NOT modify it. It copies the
committed script into a temp directory and runs it against stubs. The repository copy is
untouched and is hash-verified before and after.

WHAT IT MEASURES
----------------
  A  .env present, every health check SUCCEEDS  -> baseline
  B  .env present, every health check FAILS     -> THE TEST: is exit 0 + success banner?
  C  .env ABSENT,  every health check succeeds  -> is missing config silently tolerated?
  D  no image argument supplied                 -> does it default to the mutable `latest`?

Stubs are written with explicit LF newlines: Phase 32 recorded that a CRLF checkout artifact
broke harness invocation, which was a local artifact and not a repository defect.

READ-ONLY with respect to the repository. Everything runs in a system temp directory that is
deleted afterwards. No cluster, no network, no AWS, no deployment.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_SCRIPT = os.path.join("scripts", "deploy.sh")
SUCCESS_BANNER = "complete!"


def _write(path: str, text: str, executable: bool = False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if executable:
        os.chmod(path, 0o755)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def make_stubs(binsep: str, curl_exit: int) -> None:
    """kubectl always succeeds; curl exits with `curl_exit`; sleep is a no-op."""
    _write(os.path.join(binsep, "kubectl"),
           '#!/bin/bash\necho "[stub kubectl] $*"\nexit 0\n', True)
    _write(os.path.join(binsep, "curl"),
           '#!/bin/bash\necho "[stub curl] $*" >&2\nexit %d\n' % curl_exit, True)
    _write(os.path.join(binsep, "sleep"), '#!/bin/bash\nexit 0\n', True)


def run_case(label: str, curl_exit: int, write_env: bool, args: list,
             expect_note: str) -> dict:
    tmp = tempfile.mkdtemp(prefix="p37_deploy_")
    try:
        binsep = os.path.join(tmp, "stubbin")
        os.makedirs(binsep)
        make_stubs(binsep, curl_exit)

        # Use the COMMITTED bytes, not the working-tree copy. On this Windows checkout
        # `core.autocrlf` has rewritten scripts/deploy.sh with 82 CRLF pairs, which makes
        # bash abort at `set -e\r` ("set: - : invalid option") before reaching the health
        # loop. Phase 32 recorded that as a local checkout artifact, not a repository
        # defect, and the committed blob is LF. Testing the worktree copy produces a false
        # negative, which this probe's first run demonstrated.
        committed = subprocess.run(
            ["git", "show", "HEAD:scripts/deploy.sh"],
            cwd=os.getcwd(), capture_output=True, timeout=60,
        ).stdout.replace(b"\r\n", b"\n")
        with open(os.path.join(tmp, "deploy.sh"), "wb") as fh:
            fh.write(committed)
        os.chmod(os.path.join(tmp, "deploy.sh"), 0o755)

        if write_env:
            _write(os.path.join(tmp, ".env.staging"),
                   "# comment line\nAPP_ENV=staging\nFEATURE_FLAG=on\n")

        env = dict(os.environ)
        env["PATH"] = binsep + os.pathsep + env.get("PATH", "")

        # Capture BYTES, not text: the script emits emoji (rocket, check, hourglass,
        # party) and the Windows default `charmap` codec raises UnicodeDecodeError on
        # 0x8f, which silently empties the captured output and fakes a clean result.
        proc = subprocess.run(
            ["bash", "./deploy.sh"] + args,
            cwd=tmp, env=env, capture_output=True, timeout=120,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
              (proc.stderr or b"").decode("utf-8", "replace")
        return {
            "label": label,
            "exit": proc.returncode,
            "banner": SUCCESS_BANNER in out,
            "health_passed_msg": "Health check passed" in out,
            "attempts": out.count("Health check attempt"),
            "image_line": next((l.strip() for l in out.splitlines()
                                if l.strip().startswith("Image:")), "(none)"),
            "note": expect_note,
            "tail": [l for l in out.splitlines() if l.strip()][-1:] or ["(no output)"],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not os.path.exists(REPO_SCRIPT):
        print("FATAL: %s not found; run from the repository root." % REPO_SCRIPT)
        return 2

    before = _sha256(REPO_SCRIPT)
    print("=" * 100)
    print("Phase 37 — isolated reproduction: scripts/deploy.sh health-check failure semantics")
    print("=" * 100)
    print("repository script sha256 (before) : %s" % before)
    print("NOTE: the repository copy is never modified; each case runs on a temp copy.\n")

    cases = [
        ("A  all health checks SUCCEED", 0, True, [], "baseline: expect exit 0 + banner"),
        ("B  all health checks FAIL", 7, True, [], "THE TEST: does it still exit 0 + banner?"),
        ("C  .env.staging ABSENT", 0, False, [], "is missing config tolerated?"),
        ("D  no image argument", 0, True, [], "does IMAGE_TAG default to `latest`?"),
    ]

    results = [run_case(*c) for c in cases]

    print("%-30s %6s %8s %9s %9s  %s" %
          ("case", "exit", "banner", "hc-passed", "attempts", "image"))
    print("-" * 100)
    for r in results:
        print("%-30s %6d %8s %9s %9d  %s" %
              (r["label"], r["exit"], r["banner"], r["health_passed_msg"],
               r["attempts"], r["image_line"]))

    a = next(r for r in results if r["label"].startswith("A"))
    b = next(r for r in results if r["label"].startswith("B"))
    c = next(r for r in results if r["label"].startswith("C"))
    d = next(r for r in results if r["label"].startswith("D"))

    print("\n" + "=" * 100)
    print("FINDINGS")
    print("-" * 100)

    false_green = (b["exit"] == 0 and b["banner"] and not b["health_passed_msg"])
    indistinguishable = (a["exit"] == b["exit"] and a["banner"] == b["banner"])

    print("1. All-fail case exit code                   : %d" % b["exit"])
    print("   All-fail case printed success banner      : %s" % b["banner"])
    print("   All-fail case printed 'health check passed': %s" % b["health_passed_msg"])
    print("   Health-check attempts observed            : %d" % b["attempts"])
    print("   -> FALSE GREEN REPRODUCED                 : %s" % false_green)
    print("2. Success and total-failure indistinguishable by exit code + banner : %s"
          % indistinguishable)
    print("3. Missing .env.staging  -> exit %d, banner %s  (silently tolerated: %s)"
          % (c["exit"], c["banner"], c["exit"] == 0))
    print("4. Default image reference                   : %s" % d["image_line"])

    after = _sha256(REPO_SCRIPT)
    print("\nrepository script sha256 (after)  : %s" % after)
    print("repository script UNCHANGED       : %s" % (before == after))

    print("\nNOT DONE: no cluster contacted, no deployment performed, no repository file")
    print("modified, no network or AWS call. kubectl/curl/sleep were stubs on PATH only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
