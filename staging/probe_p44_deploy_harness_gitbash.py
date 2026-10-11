"""Phase 44 — CALIBRATED deployment harness on Git Bash, with valid positive control.

WHY THIS EXISTS
---------------
Phase 37 reproduced the all-fail false green but had no valid all-pass control.
Phase 42 concluded no POSIX shell was available and marked the lane BLOCKED.

Phase 42 was WRONG about the shell. It only inspected `bash` on PATH, which resolves to
`C:\\Windows\\system32\\bash.exe` (a WSL launcher whose distribution fails to start). A fully
working **Git Bash** ships with the portable Git installation in this tree:

    C:\\aerora_quant_backend_updated_final1\\Git\\bin\\bash.exe
    GNU bash, version 5.3.15(1)-release (x86_64-pc-cygwin)

This harness uses it, self-validates with environment controls BEFORE interpreting any
script result, and proves stub invocation with marker files.

No grant exists for `scripts/deploy.sh`, so it is NOT modified. The committed blob is copied
into a temp directory; the repository file's sha256 is checked before and after.
No cluster, no network, no AWS, no deployment.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASH = r"C:\aerora_quant_backend_updated_final1\Git\bin\bash.exe"
REPO_SCRIPT = os.path.join("scripts", "deploy.sh")
BANNER = "complete!"
TIMEOUT = 120


def sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def committed_bytes() -> bytes:
    out = subprocess.run(["git", "show", "HEAD:scripts/deploy.sh"],
                         capture_output=True, timeout=60).stdout
    return out.replace(b"\r\n", b"\n")


def w(path: str, text: str, ex: bool = False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if ex:
        os.chmod(path, 0o755)


def bash(cmd: str, cwd: str):
    try:
        p = subprocess.run([BASH, "-c", cmd], cwd=cwd,
                           capture_output=True, timeout=TIMEOUT)
        return (p.returncode,
                (p.stdout or b"").decode("utf-8", "replace") +
                (p.stderr or b"").decode("utf-8", "replace"),
                False)
    except subprocess.TimeoutExpired:
        return None, "", True


CURL_FAIL = '#!/bin/sh\necho "curl $*" >> ./markers/curl.calls\nexit 7\n'
CURL_PASS = '#!/bin/sh\necho "curl $*" >> ./markers/curl.calls\nexit 0\n'
CURL_TRANSIENT = (
    '#!/bin/sh\n'
    'echo "curl $*" >> ./markers/curl.calls\n'
    'n=$(wc -l < ./markers/curl.calls)\n'
    'if [ "$n" -lt 3 ]; then exit 7; fi\n'
    'exit 0\n'
)

SAFE_OLD = """for i in {1..5}; do
    if curl -sf $HEALTH_URL > /dev/null; then
        echo "   \u2713 Health check passed"
        break
    fi
    echo "   \u23f3 Health check attempt $i/5..."
    sleep 10
done"""
SAFE_NEW = """HEALTH_OK=0
for i in {1..5}; do
    if curl -sf $HEALTH_URL > /dev/null; then
        echo "   \u2713 Health check passed"
        HEALTH_OK=1
        break
    fi
    echo "   \u23f3 Health check attempt $i/5..."
    sleep 10
done
if [ "$HEALTH_OK" -ne 1 ]; then
    echo "\u274c Health checks failed after 5 attempts; refusing to report success."
    exit 1
fi"""


def controls() -> bool:
    """Environment controls. No script result is admissible unless these pass."""
    tmp = tempfile.mkdtemp(prefix="p44_ctl_")
    try:
        ok = []
        rc, out, _ = bash("echo P44_OK", tmp)
        ok.append(("shell startup", rc == 0 and "P44_OK" in out))
        rc, _, _ = bash("exit 23", tmp)
        ok.append(("negative exit", rc == 23))
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "probe"),
          '#!/bin/sh\necho "probe $*" >> ./markers/probe.calls\nexit 0\n', True)
        rc, out, _ = bash('export PATH="$PWD/stubbin:$PATH"; probe a1 b2', tmp)
        mk = os.path.join(tmp, "markers", "probe.calls")
        content = open(mk).read().strip() if os.path.exists(mk) else ""
        ok.append(("stub resolution", rc == 0 and "probe a1 b2" in content))
        w(os.path.join(tmp, "fail.sh"), '#!/bin/bash\nexit 9\n', True)
        rc, _, _ = bash("./fail.sh", tmp)
        ok.append(("failing script detected", rc == 9))
        w(os.path.join(tmp, "pass.sh"), '#!/bin/bash\necho fine\nexit 0\n', True)
        rc, out, _ = bash("./pass.sh", tmp)
        ok.append(("passing script detected", rc == 0 and "fine" in out))

        print("ENVIRONMENT CONTROLS")
        print("-" * 96)
        for name, good in ok:
            print("  [%s] %s" % ("x" if good else " ", name))
        allok = all(g for _, g in ok)
        print("  -> controls %s" % ("PASS" if allok else "FAIL"))
        return allok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_case(label: str, curl: str, env: bool, args: list, safe: bool) -> dict:
    tmp = tempfile.mkdtemp(prefix="p44_dep_")
    try:
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "kubectl"),
          '#!/bin/sh\necho "kubectl $*" >> ./markers/kubectl.calls\nexit 0\n', True)
        w(os.path.join(tmp, "stubbin", "curl"), curl, True)
        w(os.path.join(tmp, "stubbin", "sleep"),
          '#!/bin/sh\necho "sleep $*" >> ./markers/sleep.calls\nexit 0\n', True)

        src = committed_bytes().decode("utf-8")
        patched = False
        if safe and SAFE_OLD in src:
            src = src.replace(SAFE_OLD, SAFE_NEW)
            patched = True
        w(os.path.join(tmp, "deploy.sh"), src, True)

        if env:
            w(os.path.join(tmp, ".env.staging"),
              '# comment\nAPP_ENV=staging\nPLAIN=value\n')

        cmd = 'export PATH="$PWD/stubbin:$PATH"; ./deploy.sh' + "".join(" " + a for a in args)
        rc, out, tmo = bash(cmd, tmp)

        def count(f: str) -> int:
            p = os.path.join(tmp, "markers", f)
            if not os.path.exists(p):
                return 0
            return len([l for l in open(p, encoding="utf-8", errors="replace") if l.strip()])

        img = "(none)"
        kp = os.path.join(tmp, "markers", "kubectl.calls")
        if os.path.exists(kp):
            for line in open(kp, encoding="utf-8", errors="replace"):
                m = re.search(r"backend=(\S+)", line)
                if m:
                    img = m.group(1)
        return {"label": label, "patched": patched, "tmo": tmo, "exit": rc,
                "banner": BANNER in out, "hc": "Health check passed" in out,
                "attempts": out.count("Health check attempt"),
                "curl": count("curl.calls"), "kubectl": count("kubectl.calls"),
                "image": img}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    before = sha256_file(REPO_SCRIPT)
    print("=" * 96)
    print("Phase 44 — CALIBRATED deployment harness (Git Bash)")
    print("=" * 96)
    print("bash      : %s" % BASH)
    print("repo sha  : %s (before)" % before)
    print("source    : COMMITTED blob (LF), not the CRLF worktree copy\n")

    if not controls():
        print("\nABORT: environment controls failed; no script result is admissible.")
        return 1

    cases = [
        ("REAL all health fail",   CURL_FAIL,      True,  [], False),
        ("REAL first health pass", CURL_PASS,      True,  [], False),
        ("REAL transient->pass",   CURL_TRANSIENT, True,  [], False),
        ("REAL .env absent",       CURL_PASS,      False, [], False),
        ("REAL no image arg",      CURL_PASS,      True,  [], False),
        ("SAFE all health fail",   CURL_FAIL,      True,  [], True),
        ("SAFE first health pass", CURL_PASS,      True,  [], True),
    ]
    rows = [run_case(*c) for c in cases]

    print("\nDEPLOYMENT SCENARIOS")
    print("-" * 96)
    print("%-24s %5s %7s %6s %7s %5s %8s  %s" %
          ("case", "exit", "banner", "hcpass", "attempts", "curl", "kubectl", "image"))
    for r in rows:
        print("%-24s %5s %7s %6s %7d %5d %8d  %s" %
              (r["label"], "TMO" if r["tmo"] else r["exit"], r["banner"], r["hc"],
               r["attempts"], r["curl"], r["kubectl"], r["image"]))

    g = {r["label"]: r for r in rows}
    rf, rp, rt = g["REAL all health fail"], g["REAL first health pass"], g["REAL transient->pass"]
    sf, sp = g["SAFE all health fail"], g["SAFE first health pass"]

    print("\nCALIBRATION VERDICT")
    print("-" * 96)
    stub = rf["curl"] > 0 and rf["kubectl"] > 0
    pos = rp["exit"] == 0 and rp["hc"] and rp["curl"] == 1
    neg = rf["exit"] == 0 and rf["banner"] and not rf["hc"]
    disc = sf["patched"] and sf["exit"] not in (0, None) and not sf["banner"] \
        and sp["exit"] == 0 and sp["banner"]
    print("  stub invocation proven        : %s (curl=%d kubectl=%d)"
          % (stub, rf["curl"], rf["kubectl"]))
    print("  POSITIVE control valid        : %s (exit %s, hc-pass %s, 1 curl)"
          % (pos, rp["exit"], rp["hc"]))
    print("  retry control                 : exit %s, hc-pass %s, %d attempts, %d curl"
          % (rt["exit"], rt["hc"], rt["attempts"], rt["curl"]))
    print("  NEGATIVE control (defect)     : %s (exit %s + banner %s, no pass)"
          % (neg, rf["exit"], rf["banner"]))
    print("  safe/unsafe discrimination    : %s" % disc)
    print("  success vs total-failure same : %s"
          % (rf["exit"] == rp["exit"] and rf["banner"] == rp["banner"]))
    print("  mutating kubectl in all-fail  : %s (image %s)"
          % (rf["kubectl"] > 0, rf["image"]))

    after = sha256_file(REPO_SCRIPT)
    print("\nrepo sha  : %s (after)" % after)
    print("repository UNCHANGED : %s" % (before == after))
    print("\nHARNESS CALIBRATED : %s" % (stub and pos and neg and disc))
    print("NOT DONE: no cluster, network, AWS, deployment, or repository write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
