"""Phase 44 - ordered call trace for scripts/deploy.sh (stubs only).

QUESTION THIS ANSWERS
---------------------
The calibrated harness showed that with all five health checks failing, scripts/deploy.sh
still exits 0 and prints its success banner, and that kubectl was invoked. It did not show
WHEN the mutation happens relative to verification, nor whether anything attempts a
rollback afterwards. Those two facts decide whether the defect is "reports success wrongly"
or "mutates the cluster, cannot verify it, and walks away claiming success".

This probe records every external command in one ordered log so the sequence is observable.

SAFETY
------
Stubs only, first on PATH, inside a throwaway temp dir. kubectl is not installed on this
machine. No cluster, no network, no deployment. scripts/deploy.sh is NOT modified; the
committed blob is copied to temp and the repository file's sha256 is checked before/after.
Zero stub invocations is reported NOT RUN, never as a result.
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

BASH = r"C:\aerora_quant_backend_updated_final1\Git\bin\bash.exe"
REPO_SCRIPT = os.path.join("scripts", "deploy.sh")

# One shared, append-only ordered log. Every stub writes to it, so the interleaving of
# mutations and verifications is directly observable rather than inferred.
TRACE = r"""#!/bin/sh
echo "%s $*" >> ./markers/trace.log
"""


def sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def w(path: str, text: str, ex: bool = False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if ex:
        os.chmod(path, 0o755)


def trace_case(label: str, curl_rc: int) -> dict:
    tmp = tempfile.mkdtemp(prefix="p44o_")
    try:
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "kubectl"),
          (TRACE % "kubectl") + "exit 0\n", True)
        w(os.path.join(tmp, "stubbin", "curl"),
          (TRACE % "curl") + ("exit %d\n" % curl_rc), True)
        w(os.path.join(tmp, "stubbin", "sleep"),
          (TRACE % "sleep") + "exit 0\n", True)
        w(os.path.join(tmp, "stubbin", "docker"),
          (TRACE % "docker") + "exit 0\n", True)

        blob = subprocess.run(["git", "show", "HEAD:scripts/deploy.sh"],
                              capture_output=True, timeout=60).stdout
        w(os.path.join(tmp, "deploy.sh"),
          blob.replace(b"\r\n", b"\n").decode("utf-8", "replace"), True)
        w(os.path.join(tmp, ".env.staging"), "# c\nAPP_ENV=staging\n")

        p = subprocess.run(
            [BASH, "-c", 'export PATH="$PWD/stubbin:$PATH"; ./deploy.sh staging v1.2.3'],
            cwd=tmp, capture_output=True, timeout=180)
        out = ((p.stdout or b"").decode("utf-8", "replace") +
               (p.stderr or b"").decode("utf-8", "replace"))

        tp = os.path.join(tmp, "markers", "trace.log")
        trace = ([l.rstrip("\n") for l in open(tp, encoding="utf-8", errors="replace")
                  if l.strip()] if os.path.exists(tp) else [])
        return {"label": label, "exit": p.returncode, "trace": trace,
                "banner": "complete!" in out, "stub_proven": len(trace) > 0}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    before = sha256_file(REPO_SCRIPT)
    print("=" * 96)
    print("Phase 44 - ordered call trace, scripts/deploy.sh")
    print("=" * 96)
    print("repo sha : %s (before)\n" % before)

    for label, rc in (("all health checks FAIL", 7), ("health checks PASS", 0)):
        r = trace_case(label, rc)
        print("CASE: %s  -> exit %s, banner %s" % (label, r["exit"], r["banner"]))
        print("-" * 96)
        if not r["stub_proven"]:
            print("  NOT RUN (zero stub invocations); exit code is not interpretable.\n")
            continue
        mut_at = None
        first_verify_at = None
        for i, line in enumerate(r["trace"], 1):
            kind = ""
            if line.startswith("kubectl set image"):
                kind = "  <== MUTATION"
                if mut_at is None:
                    mut_at = i
            elif line.startswith("curl"):
                kind = "  <== verification"
                if first_verify_at is None:
                    first_verify_at = i
            elif line.startswith("kubectl rollout undo") or "undo" in line:
                kind = "  <== ROLLBACK"
            print("  %2d. %s%s" % (i, line, kind))
        print("  mutation step            : %s" % mut_at)
        print("  first verification step  : %s" % first_verify_at)
        print("  mutation BEFORE verify   : %s"
              % (mut_at is not None and first_verify_at is not None
                 and mut_at < first_verify_at))
        print("  any rollback attempted   : %s"
              % any("undo" in l or "rollback" in l for l in r["trace"]))
        print("")

    after = sha256_file(REPO_SCRIPT)
    print("repo sha : %s (after)" % after)
    print("repository UNCHANGED : %s" % (before == after))
    print("NOT DONE: no cluster, no network, no deployment, no repo write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
