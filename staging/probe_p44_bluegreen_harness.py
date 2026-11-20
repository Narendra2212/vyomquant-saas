"""Phase 44 - CALIBRATED harness for scripts/blue-green-deploy.sh (stubs only).

QUESTION THIS ANSWERS
---------------------
Three read helpers swallow failure and substitute the literal string "unknown":
    :50 get_active_color  -> kubectl get service   ... || echo "unknown"
    :54 get_green_image   -> kubectl get deployment... || echo "unknown"
    :58 get_blue_image    -> kubectl get deployment... || echo "unknown"
`promote()` reads GREEN_IMAGE at :236 and writes it to the BLUE deployment at :247-249
with no check that it is a real image reference. Prior phases never traced the
rollback/promote branches. This harness asks, by execution: can "unknown" reach a write?

SAFETY
------
kubectl is NOT installed on this machine, and a stub kubectl is placed first on PATH
regardless. Every invocation is recorded; mutating verbs (set image / scale / patch) are
recorded separately in ./markers/writes.calls and are never forwarded anywhere. No
cluster, no network, no deployment. `scripts/blue-green-deploy.sh` is NOT modified: the
committed blob is copied to a temp dir and the repository file's sha256 is checked before
and after. A case with zero stub invocations is reported NOT RUN, never as a result.
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
REPO_SCRIPT = os.path.join("scripts", "blue-green-deploy.sh")
TIMEOUT = 180

KUBECTL_STUB = r"""#!/bin/sh
echo "kubectl $*" >> ./markers/kubectl.calls
case "$1" in
  get)
    name="$3"
    case "$*" in
      *selector.color*)
          if [ "${P44_SVC_READ_FAIL:-0}" = "1" ]; then exit 1; fi
          if [ -f ./markers/color.state ]; then cat ./markers/color.state; exit 0; fi
          printf '%s\n' "${P44_ACTIVE:-blue}" ; exit 0 ;;
      *containers*image*)
          case "$name" in
            *green*)
                if [ "${P44_GREEN_READ_FAIL:-0}" = "1" ]; then exit 1; fi
                printf '%s\n' "${P44_GREEN_IMAGE:-registry.invalid/app:v2}" ; exit 0 ;;
            *blue*)
                if [ "${P44_BLUE_READ_FAIL:-0}" = "1" ]; then exit 1; fi
                printf '%s\n' "${P44_BLUE_IMAGE:-registry.invalid/app:v1}" ; exit 0 ;;
          esac
          exit 1 ;;
      *spec.replicas*)
          if [ "${P44_REPL_READ_FAIL:-0}" = "1" ]; then exit 1; fi
          printf '%s\n' "${P44_REPLICAS:-3}" ; exit 0 ;;
      *readyReplicas*)
          printf '%s\n' "${P44_READY_REPLICAS:-3}" ; exit 0 ;;
      *pods*)
          echo "pod/trading-backend-stub   1/1   Running" ; exit 0 ;;
    esac
    exit 0 ;;
  set)
    echo "WRITE set-image $*" >> ./markers/writes.calls ; exit 0 ;;
  scale)
    echo "WRITE scale $*" >> ./markers/writes.calls ; exit 0 ;;
  patch)
    echo "WRITE patch $*" >> ./markers/writes.calls
    if [ "${P44_PATCH_EFFECTIVE:-1}" = "1" ]; then
      case "$*" in
        *\"green\"*) printf 'green\n' > ./markers/color.state ;;
        *\"blue\"*)  printf 'blue\n'  > ./markers/color.state ;;
      esac
    fi
    exit 0 ;;
  rollout)
    if [ "${P44_ROLLOUT_FAIL:-0}" = "1" ]; then
      echo "error: deployment exceeded its progress deadline" >&2
      exit 1
    fi
    exit 0 ;;
  wait)    exit 0 ;;
  exec)
    case "$*" in
      *health/ready*)
          if [ "${P44_READY_OK:-1}" = "1" ]; then exit 0; fi
          exit 1 ;;
      *health*)
          echo "hc" >> ./markers/hc.calls
          n=$(wc -l < ./markers/hc.calls)
          if [ -n "${P44_HEALTH_FAIL_AT:-}" ] && [ "$n" -ge "${P44_HEALTH_FAIL_AT}" ]; then
            exit 1
          fi
          exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

SLEEP_STUB = '#!/bin/sh\necho "sleep $*" >> ./markers/sleep.calls\nexit 0\n'
CURL_STUB = '#!/bin/sh\necho "curl $*" >> ./markers/curl.calls\nexit 0\n'


def sha256_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def committed(path: str) -> str:
    out = subprocess.run(["git", "show", "HEAD:" + path],
                         capture_output=True, timeout=60).stdout
    return out.replace(b"\r\n", b"\n").decode("utf-8", "replace")


def w(path: str, text: str, ex: bool = False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if ex:
        os.chmod(path, 0o755)


def controls() -> bool:
    tmp = tempfile.mkdtemp(prefix="p44b_ctl_")
    try:
        ok = []
        p = subprocess.run([BASH, "-c", "echo P44B_OK"], cwd=tmp,
                           capture_output=True, timeout=60)
        ok.append(("shell startup", p.returncode == 0 and b"P44B_OK" in p.stdout))
        p = subprocess.run([BASH, "-c", "exit 17"], cwd=tmp,
                           capture_output=True, timeout=60)
        ok.append(("negative exit", p.returncode == 17))
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "kubectl"), KUBECTL_STUB, True)
        p = subprocess.run(
            [BASH, "-c", 'export PATH="$PWD/stubbin:$PATH"; command -v kubectl;'
                         ' kubectl get service s -o jsonpath=.spec.selector.color'],
            cwd=tmp, capture_output=True, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        mk = os.path.join(tmp, "markers", "kubectl.calls")
        ok.append(("stub kubectl resolves + records",
                   "stubbin/kubectl" in out and "blue" in out and os.path.exists(mk)))
        # The read helpers must actually yield "unknown" when the read fails.
        env = os.environ.copy()
        env["P44_SVC_READ_FAIL"] = "1"
        p = subprocess.run(
            [BASH, "-c", 'export PATH="$PWD/stubbin:$PATH";'
                         ' v=$(kubectl get service s -o jsonpath=.spec.selector.color'
                         ' 2>/dev/null || echo unknown); echo "GOT:$v"'],
            cwd=tmp, env=env, capture_output=True, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        ok.append(('failed read degrades to "unknown"', "GOT:unknown" in out))

        print("ENVIRONMENT CONTROLS")
        print("-" * 112)
        for name, good in ok:
            print("  [%s] %s" % ("x" if good else " ", name))
        allok = all(g for _, g in ok)
        print("  -> controls %s" % ("PASS" if allok else "FAIL"))
        return allok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_case(label: str, argv: list, scen: dict, expect_no_kubectl: bool = False) -> dict:
    tmp = tempfile.mkdtemp(prefix="p44b_bg_")
    try:
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "kubectl"), KUBECTL_STUB, True)
        w(os.path.join(tmp, "stubbin", "sleep"), SLEEP_STUB, True)
        w(os.path.join(tmp, "stubbin", "curl"), CURL_STUB, True)
        w(os.path.join(tmp, "bg.sh"), committed("scripts/blue-green-deploy.sh"), True)

        env = os.environ.copy()
        env["NAMESPACE"] = "p44-sandbox"
        env.pop("SLACK_WEBHOOK_URL", None)
        for k, v in scen.items():
            env[k] = str(v)

        cmd = ('export PATH="$PWD/stubbin:$PATH"; ./bg.sh'
               + "".join(" " + a for a in argv))
        try:
            p = subprocess.run([BASH, "-c", cmd], cwd=tmp, env=env,
                               capture_output=True, timeout=TIMEOUT)
            rc = p.returncode
            out = ((p.stdout or b"").decode("utf-8", "replace") +
                   (p.stderr or b"").decode("utf-8", "replace"))
            tmo = False
        except subprocess.TimeoutExpired:
            rc, out, tmo = None, "", True

        def lines(f: str) -> list:
            path = os.path.join(tmp, "markers", f)
            if not os.path.exists(path):
                return []
            return [l.rstrip("\n") for l in
                    open(path, encoding="utf-8", errors="replace") if l.strip()]

        writes = lines("writes.calls")
        return {
            "label": label,
            "exit": rc,
            "tmo": tmo,
            "kubectl": len(lines("kubectl.calls")),
            "writes": len(writes),
            "write_lines": writes,
            "unknown_write": any("unknown" in x for x in writes),
            "hc": len(lines("hc.calls")),
            # Default rule: zero stub invocations means the script never ran, so the
            # outcome is NOT RUN regardless of exit code. Exemption: a case whose CORRECT
            # behaviour is to refuse before any kubectl call (e.g. the empty-tag guard at
            # :68-71) legitimately records zero calls, so it must be exempted explicitly
            # or the calibration flag raises a false alarm.
            "expect_no_kubectl": expect_no_kubectl,
            "stub_proven": expect_no_kubectl or len(lines("kubectl.calls")) > 0,
            "out": out,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    before = sha256_file(REPO_SCRIPT)
    print("=" * 112)
    print("Phase 44 - CALIBRATED harness for scripts/blue-green-deploy.sh (stubs only)")
    print("=" * 112)
    print("bash     : %s" % BASH)
    print("repo sha : %s (before)" % before)
    print("source   : COMMITTED blob (LF). kubectl is NOT installed; a stub is used.\n")

    if not controls():
        print("\nABORT: environment controls failed; no script result is admissible.")
        return 1

    cases = [
        # positive controls
        ("POS deploy happy path", ["deploy", "registry.invalid/app:v2"],
         dict(P44_ACTIVE="blue")),
        ("POS switch happy path", ["switch"], dict(P44_ACTIVE="blue")),
        ("POS promote happy path", ["promote"], dict(P44_ACTIVE="green")),
        # guard controls (must refuse, zero writes)
        ("GUARD deploy empty tag", ["deploy"], dict(P44_ACTIVE="blue"), True),
        ("GUARD rollback when blue", ["rollback"], dict(P44_ACTIVE="blue")),
        ("GUARD promote when blue", ["promote"], dict(P44_ACTIVE="blue")),
        # the question: can "unknown" reach a write?
        ("UNKNOWN promote green-read-fail", ["promote"],
         dict(P44_ACTIVE="green", P44_GREEN_READ_FAIL=1)),
        # Bounds the blast radius of B1: in a real cluster an unpullable image would make
        # `kubectl rollout status` (:257) fail, which under set -e aborts BEFORE the
        # traffic patch (:260) and the green teardown (:266). This case forces that.
        ("UNKNOWN promote + rollout fails", ["promote"],
         dict(P44_ACTIVE="green", P44_GREEN_READ_FAIL=1, P44_ROLLOUT_FAIL=1)),
        ("UNKNOWN switch all-reads-fail", ["switch"], dict(P44_SVC_READ_FAIL=1)),
        ("UNKNOWN rollback all-reads-fail", ["rollback"], dict(P44_SVC_READ_FAIL=1)),
        ("UNKNOWN status all-reads-fail", ["status"], dict(P44_SVC_READ_FAIL=1)),
        # strictness of validate_green
        ("VALIDATE health fails at 1", ["deploy", "registry.invalid/app:v2"],
         dict(P44_ACTIVE="blue", P44_HEALTH_FAIL_AT=1)),
        ("VALIDATE health fails at 5", ["deploy", "registry.invalid/app:v2"],
         dict(P44_ACTIVE="blue", P44_HEALTH_FAIL_AT=5)),
        ("VALIDATE readiness fails", ["deploy", "registry.invalid/app:v2"],
         dict(P44_ACTIVE="blue", P44_READY_OK=0)),
        # mutable tag acceptance (image provenance lane)
        ("PROV deploy accepts latest", ["deploy", "latest"], dict(P44_ACTIVE="blue")),
        # set -e behaviour on an unguarded command substitution
        ("SETE replicas read fails", ["deploy", "registry.invalid/app:v2"],
         dict(P44_ACTIVE="blue", P44_REPL_READ_FAIL=1)),
        # patch accepted but selector never changes
        ("switch patch ineffective", ["switch"],
         dict(P44_ACTIVE="blue", P44_PATCH_EFFECTIVE=0)),
    ]
    rows = [run_case(*c) for c in cases]

    print("\nSCENARIOS")
    print("-" * 112)
    print("%-34s %5s %8s %7s %4s %9s  %s" %
          ("case", "exit", "kubectl", "writes", "hc", "unkn-write", "note"))
    for r in rows:
        print("%-34s %5s %8d %7d %4d %9s  %s" %
              (r["label"], "TMO" if r["tmo"] else r["exit"], r["kubectl"],
               r["writes"], r["hc"], r["unknown_write"],
               "" if r["stub_proven"] else "NOT RUN (0 stub calls)"))

    g = {r["label"]: r for r in rows}
    print("\nCALIBRATION VERDICT")
    print("-" * 112)
    stub_ok = all(r["stub_proven"] for r in rows)
    pos_ok = (g["POS deploy happy path"]["exit"] == 0
              and g["POS switch happy path"]["exit"] == 0
              and g["POS promote happy path"]["exit"] == 0)
    guard_ok = all(g[k]["writes"] == 0 for k in
                   ("GUARD deploy empty tag", "GUARD rollback when blue",
                    "GUARD promote when blue"))
    print("  stub invocation proven in ALL cases : %s" % stub_ok)
    print("  POSITIVE controls succeed           : %s" % pos_ok)
    print("  GUARD cases perform ZERO writes     : %s" % guard_ok)
    print("  CALIBRATED                          : %s" % (stub_ok and pos_ok and guard_ok))

    up = g["UNKNOWN promote green-read-fail"]
    us = g["UNKNOWN switch all-reads-fail"]
    ur = g["UNKNOWN rollback all-reads-fail"]
    ust = g["UNKNOWN status all-reads-fail"]
    v1 = g["VALIDATE health fails at 1"]
    v5 = g["VALIDATE health fails at 5"]
    vr = g["VALIDATE readiness fails"]
    prov = g["PROV deploy accepts latest"]
    sete = g["SETE replicas read fails"]
    inef = g["switch patch ineffective"]
    pd = g["POS deploy happy path"]

    print("\nFINDINGS (admissible only because the controls above pass)")
    print("-" * 112)
    print("  B1 promote writes \"unknown\" to BLUE  : %s (exit %s, writes %d)"
          % (up["unknown_write"], up["exit"], up["writes"]))
    for line in up["write_lines"]:
        print("       | %s" % line)
    print("  B2 switch with unreadable selector   : exit %s, writes %d, claims-BLUE %s"
          % (us["exit"], us["writes"], "Current active: BLUE" in us["out"]))
    print("  B3 rollback with unreadable selector : exit %s, writes %d, reports-failure %s"
          % (ur["exit"], ur["writes"], "Rollback failed" in ur["out"]))
    print("  B4 status with unreadable selector   : exit %s, writes %d" % (ust["exit"], ust["writes"]))
    print("  B5 validate_green is FAIL-CLOSED     : fail@1 exit %s / fail@5 exit %s / ready-fail exit %s"
          % (v1["exit"], v5["exit"], vr["exit"]))
    print("  B6 validate_green demands N passes   : happy path health calls = %d" % pd["hc"])
    print("  B7 mutable tag 'latest' accepted     : exit %s, writes %d"
          % (prov["exit"], prov["writes"]))
    print("  B8 unguarded $(kubectl) + set -e     : exit %s, writes %d"
          % (sete["exit"], sete["writes"]))
    for line in sete["write_lines"]:
        print("       | %s" % line)
    upr = g["UNKNOWN promote + rollout fails"]
    print("  B10 blast radius when rollout fails  : exit %s, writes %d (patch reached: %s)"
          % (upr["exit"], upr["writes"],
             any("patch" in x for x in upr["write_lines"])))
    for line in upr["write_lines"]:
        print("       | %s" % line)
    print("  B9 ineffective patch detected        : exit %s, reports-failure %s"
          % (inef["exit"], "Switch failed" in inef["out"]))

    print("\nEVIDENCE EXCERPT - UNKNOWN promote green-read-fail")
    print("-" * 112)
    for line in [l for l in up["out"].splitlines() if l.strip()][:14]:
        print("  | %s" % line)

    after = sha256_file(REPO_SCRIPT)
    print("\nrepo sha : %s (after)" % after)
    print("repository UNCHANGED : %s" % (before == after))
    print("NOT DONE: no cluster, no kubectl, no network, no deployment, no repo write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
