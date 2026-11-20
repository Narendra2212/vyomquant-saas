"""Phase 42 — CALIBRATED deployment harness: positive control, negative control, stub proof.

WHAT PHASE 37 LEFT INCOMPLETE
-----------------------------
Phase 37 reproduced the all-fail false green but could not establish a valid all-pass control,
because stub resolution was ambiguous. Phase 42 section 3.4 requires that the harness be proven
to discriminate before its results are trusted.

ROOT CAUSE OF THE AMBIGUITY, diagnosed this phase
-------------------------------------------------
`bash` on this host is **WSL** (`C:\\Windows\\system32\\bash.exe`). A Windows-style directory
(`C:\\Users\\...\\Temp\\...`) prepended to PATH is meaningless inside WSL, so the stubs were
never guaranteed to resolve. This harness therefore never puts a Windows path in PATH: it has
**bash itself** compute the stub directory via `$PWD`, and every stub writes a **marker file**
so invocation is proven rather than assumed.

WHAT IT ESTABLISHES
-------------------
  * STUB PROOF     - marker files show which stubs actually ran, and how many times.
  * NEGATIVE CTRL  - the real committed script must FAIL the safety assertion.
  * POSITIVE CTRL  - a temporary SAFE variant must PASS it, and a healthy path must still
                     succeed. The safe variant exists only in a temp dir; it is never written
                     to the repository.

No grant exists for `scripts/deploy.sh`, so the repository copy is NOT modified. Its sha256 is
checked before and after. No cluster, no network, no AWS, no deployment.
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

REPO_SCRIPT = os.path.join("scripts", "deploy.sh")
BANNER = "complete!"
PER_CASE_TIMEOUT = 90


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def committed_bytes() -> bytes:
    """The committed blob, LF-normalised. The worktree copy is CRLF (core.autocrlf=true)."""
    out = subprocess.run(["git", "show", "HEAD:scripts/deploy.sh"],
                         capture_output=True, timeout=60).stdout
    return out.replace(b"\r\n", b"\n")


def write(path: str, text: str, ex: bool = False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if ex:
        os.chmod(path, 0o755)


def make_stubs(binsep: str, markers: str, curl_script: str) -> None:
    """kubectl records its argv and succeeds; curl behaviour is injected; sleep is a no-op."""
    write(os.path.join(binsep, "kubectl"),
          '#!/bin/sh\n'
          'echo "kubectl $*" >> "%s/kubectl.calls"\n'
          'exit 0\n' % markers, True)
    write(os.path.join(binsep, "curl"), curl_script % {"m": markers}, True)
    write(os.path.join(binsep, "sleep"),
          '#!/bin/sh\necho "sleep $*" >> "%s/sleep.calls"\nexit 0\n' % markers, True)


CURL_ALWAYS_FAIL = (
    '#!/bin/sh\n'
    'echo "curl $*" >> "%(m)s/curl.calls"\n'
    'exit 7\n'
)
CURL_ALWAYS_PASS = (
    '#!/bin/sh\n'
    'echo "curl $*" >> "%(m)s/curl.calls"\n'
    'exit 0\n'
)
# Fails the first two attempts, succeeds on the third.
CURL_TRANSIENT = (
    '#!/bin/sh\n'
    'echo "curl $*" >> "%(m)s/curl.calls"\n'
    'n=$(wc -l < "%(m)s/curl.calls")\n'
    'if [ "$n" -lt 3 ]; then exit 7; fi\n'
    'exit 0\n'
)

# The temporary SAFE variant: tracks the health outcome and refuses to announce success.
# Calibration only -- never written to the repository.
SAFE_PATCH_OLD = """for i in {1..5}; do
    if curl -sf $HEALTH_URL > /dev/null; then
        echo "   \u2713 Health check passed"
        break
    fi
    echo "   \u23f3 Health check attempt $i/5..."
    sleep 10
done"""
SAFE_PATCH_NEW = """HEALTH_OK=0
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


def run_case(label: str, curl_script: str, write_env: bool, args: list,
             safe_variant: bool) -> dict:
    tmp = tempfile.mkdtemp(prefix="p42_deploy_")
    try:
        binsep = os.path.join(tmp, "stubbin")
        markers = os.path.join(tmp, "markers")
        os.makedirs(binsep)
        os.makedirs(markers)
        make_stubs(binsep, "./markers", curl_script)

        src = committed_bytes().decode("utf-8")
        patched = False
        if safe_variant:
            if SAFE_PATCH_OLD in src:
                src = src.replace(SAFE_PATCH_OLD, SAFE_PATCH_NEW)
                patched = True
        write(os.path.join(tmp, "deploy.sh"), src, True)

        if write_env:
            write(os.path.join(tmp, ".env.staging"),
                  '# comment\nAPP_ENV=staging\nQUOTED="a b c"\nEMPTY=\n')

        # Let BASH compute the stub directory. No Windows path ever enters PATH.
        cmd = 'export PATH="$PWD/stubbin:$PATH"; exec ./deploy.sh' + \
              ("".join(" " + a for a in args))
        try:
            proc = subprocess.run(["bash", "-c", cmd], cwd=tmp,
                                  capture_output=True, timeout=PER_CASE_TIMEOUT)
            rc = proc.returncode
            out = (proc.stdout or b"").decode("utf-8", "replace") + \
                  (proc.stderr or b"").decode("utf-8", "replace")
            timed_out = False
        except subprocess.TimeoutExpired:
            rc, out, timed_out = None, "", True

        def nlines(name: str) -> int:
            p = os.path.join(markers, name)
            if not os.path.exists(p):
                return 0
            with open(p, encoding="utf-8", errors="replace") as fh:
                return len([l for l in fh if l.strip()])

        kubectl_calls = nlines("kubectl.calls")
        img = "(none)"
        p = os.path.join(markers, "kubectl.calls")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.search(r"backend=(\S+)", line)
                    if m:
                        img = m.group(1)
        return {
            "label": label, "safe": safe_variant, "patched": patched,
            "timed_out": timed_out, "exit": rc,
            "banner": BANNER in out,
            "hc_passed": "Health check passed" in out,
            "attempts": out.count("Health check attempt"),
            "curl_calls": nlines("curl.calls"),
            "kubectl_calls": kubectl_calls,
            "mutating_called": img != "(none)",
            "image": img,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not os.path.exists(REPO_SCRIPT):
        print("FATAL: run from the repository root.")
        return 2
    before = sha256_file(REPO_SCRIPT)

    print("=" * 104)
    print("Phase 42 — CALIBRATED deployment harness (stub-proof + positive/negative controls)")
    print("=" * 104)
    print("repo script sha256 before : %s" % before)
    print("bash                      : WSL (C:\\Windows\\system32\\bash.exe)")
    print("stub strategy             : PATH computed by bash via $PWD; stubs write marker files")
    print("source under test         : COMMITTED blob (LF), not the CRLF worktree copy\n")

    cases = [
        # label,                           curl,              env,  args, safe
        ("REAL  all health fail",          CURL_ALWAYS_FAIL,  True,  [], False),
        ("REAL  first health passes",      CURL_ALWAYS_PASS,  True,  [], False),
        ("REAL  transient then passes",    CURL_TRANSIENT,    True,  [], False),
        ("REAL  .env absent",              CURL_ALWAYS_PASS,  False, [], False),
        ("REAL  no image arg",             CURL_ALWAYS_PASS,  True,  [], False),
        ("SAFE  all health fail",          CURL_ALWAYS_FAIL,  True,  [], True),
        ("SAFE  first health passes",      CURL_ALWAYS_PASS,  True,  [], True),
    ]
    rows = [run_case(*c) for c in cases]

    print("%-28s %5s %6s %7s %6s %6s %7s  %s" % (
        "case", "exit", "banner", "hcpass", "attmpt", "curl", "kubectl", "image"))
    print("-" * 104)
    for r in rows:
        print("%-28s %5s %6s %7s %6d %6d %7d  %s" % (
            r["label"], "TMO" if r["timed_out"] else r["exit"], r["banner"],
            r["hc_passed"], r["attempts"], r["curl_calls"], r["kubectl_calls"],
            r["image"]))

    g = {r["label"]: r for r in rows}
    rf = g["REAL  all health fail"]
    rp = g["REAL  first health passes"]
    rt = g["REAL  transient then passes"]
    sf = g["SAFE  all health fail"]
    sp = g["SAFE  first health passes"]

    print("\n" + "=" * 104)
    print("CALIBRATION")
    print("-" * 104)
    stub_proof = rf["curl_calls"] > 0 and rf["kubectl_calls"] > 0
    print("1. STUB PROOF  curl invoked %d time(s), kubectl %d time(s) -> stubs resolved: %s"
          % (rf["curl_calls"], rf["kubectl_calls"], stub_proof))
    pos_ok = (rp["exit"] == 0 and rp["hc_passed"] and rp["attempts"] == 0
              and rp["curl_calls"] == 1)
    print("2. POSITIVE CONTROL  healthy path: exit %s, 'passed' %s, 1 curl call -> valid: %s"
          % (rp["exit"], rp["hc_passed"], pos_ok))
    print("3. RETRY CONTROL     transient: exit %s, 'passed' %s, %d attempt msg(s), %d curl calls"
          % (rt["exit"], rt["hc_passed"], rt["attempts"], rt["curl_calls"]))
    neg_ok = (rf["exit"] == 0 and rf["banner"] and not rf["hc_passed"])
    print("4. NEGATIVE CONTROL  REAL all-fail: exit %s + banner %s, no pass -> DEFECT: %s"
          % (rf["exit"], rf["banner"], neg_ok))
    safe_ok = (sf["patched"] and sf["exit"] not in (0, None) and not sf["banner"]
               and sp["exit"] == 0 and sp["banner"])
    print("5. DISCRIMINATION    SAFE all-fail exit %s banner %s | SAFE healthy exit %s banner %s"
          % (sf["exit"], sf["banner"], sp["exit"], sp["banner"]))
    print("                     -> harness distinguishes safe from unsafe: %s" % safe_ok)

    indistinguishable = (rf["exit"] == rp["exit"] and rf["banner"] == rp["banner"])
    print("6. REAL success vs REAL total-failure identical on exit+banner: %s"
          % indistinguishable)
    print("7. Mutating kubectl call reached in all-fail case: %s (image %s)"
          % (rf["mutating_called"], rf["image"]))

    after = sha256_file(REPO_SCRIPT)
    print("\nrepo script sha256 after  : %s" % after)
    print("repository UNCHANGED      : %s" % (before == after))

    calibrated = stub_proof and pos_ok and neg_ok and safe_ok
    print("\nHARNESS CALIBRATED: %s" % calibrated)
    print("NOT DONE: no cluster, no network, no AWS, no deployment, no repository file written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
