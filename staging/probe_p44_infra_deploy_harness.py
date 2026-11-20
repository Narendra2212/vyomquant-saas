"""Phase 44 - CALIBRATED harness for infra/deploy.sh (stubs only, no real AWS).

WHY THIS EXISTS
---------------
`infra/deploy.sh` is the "Master Orchestrator & Pre/Post-Flight Verification Engine".
Static reading says its four post-deployment checks (ALB state, target health, ECS task
counts, HTTP /health code) are computed and then never gate anything, and that
`:133 DEPLOYMENT COMPLETE & VERIFIED` and `:139 Readiness Score: 100%` are unconditional.
This harness tests those readings by execution.

SAFETY
------
The REAL aws CLI is present at C:\\Users\\aa\\aws_cli_extract\\Amazon\\AWSCLIV2\\aws.exe.
Isolation is therefore enforced three ways and verified, not assumed:
  1. a stub `aws` is placed FIRST on PATH inside a throwaway temp dir;
  2. every stub invocation is appended to ./markers/aws.calls, and the harness FAILS the
     case if the marker file is missing (that would mean the real CLI may have been used);
  3. credentials/config are redirected to nonexistent temp paths and IMDS is disabled, so
     even a leaked invocation cannot authenticate.
The provisioning sub-scripts acm.sh / alb.sh / ecs.sh are stubbed in the temp SCRIPT_DIR,
so no infrastructure is created or changed. No network, no cluster, no deployment.
`infra/deploy.sh` is NOT modified; the committed blob is copied to temp and the repository
file's sha256 is checked before and after.
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
REPO_SCRIPT = os.path.join("infra", "deploy.sh")
BANNER = "DEPLOYMENT COMPLETE & VERIFIED"
READY = "Readiness Score:  100%"
TIMEOUT = 180

# Payload that breaks out of the triple-quoted Python literal at infra/deploy.sh:59.
# Contains no double quote, no $ and no backtick, so it survives the bash double-quoted
# context unchanged. Writes a marker file instead of doing anything harmful.
INJECT = (
    "{}''') or __import__('pathlib')"
    ".Path('./markers/injected.marker').write_text('INJECTED') or json.loads('''{}"
)

# The SAME sink pattern appears at :115 and :116, where the ENTIRE describe-services JSON
# is interpolated into Python source. Two probes:
#   SVC_QUOTES  - realistic: ECS metadata merely containing ''' . Measures the fail-open
#                 degradation path (|| echo "0") rather than code execution.
#   SVC_INJECT  - engineered: a full breakout, proving the sink executes attacker code when
#                 the response is attacker-controlled (compromised/shadowed aws, tampered
#                 response). Writes a distinct marker.
SVC_QUOTES = ('{"services": [{"desiredCount": 4, "runningCount": 4, '
              '"tags": [{"key": "note", "value": "a\'\'\'b"}]}]}')
SVC_INJECT = (
    "{}''') or __import__('pathlib')"
    ".Path('./markers/injected_ecs.marker').write_text('ECS') or json.loads('''"
    '{"services": [{"desiredCount": 4, "runningCount": 4}]}'
)


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


AWS_STUB = r"""#!/bin/sh
echo "aws $*" >> ./markers/aws.calls
case "$1 $2" in
  "--version "*|"--version")
      echo "aws-cli/2.0.0-STUB" ; exit 0 ;;
esac
if [ "$1" = "--version" ]; then echo "aws-cli/2.0.0-STUB"; exit 0; fi
case "$1" in
  sts)
      if [ "${P44_AUTH_EMPTY:-0}" = "1" ]; then exit 1; fi
      if [ -n "${P44_IDENTITY_RAW:-}" ]; then printf '%s' "$P44_IDENTITY_RAW"; exit 0; fi
      printf '{"Account": "%s", "Arn": "arn:aws:iam::x:user/stub"}' "${P44_ACCOUNT:-273709947018}"
      exit 0 ;;
  acm)
      if [ "${P44_PERM_FAIL:-}" = "acm" ]; then exit 1; fi
      echo '{"CertificateSummaryList": []}' ; exit 0 ;;
  elbv2)
      if [ "${P44_PERM_FAIL:-}" = "elbv2" ]; then exit 1; fi
      case "$2" in
        describe-load-balancers)
            if [ -n "${P44_WANT_DNS:-}" ] && [ "${P44_QUERY_DNS:-0}" = "1" ]; then :; fi
            for a in "$@"; do
              case "$a" in
                *DNSName*) printf '%s\n' "${P44_ALB_DNS:-stub-alb.example.invalid}" ; exit 0 ;;
                *State.Code*) printf '%s\n' "${P44_ALB_STATE:-active}" ; exit 0 ;;
              esac
            done
            echo '{"LoadBalancers": []}' ; exit 0 ;;
        describe-target-groups)
            printf '%s\n' "${P44_TG_ARN:-arn:aws:elasticloadbalancing:stub:tg/api}" ; exit 0 ;;
        describe-target-health)
            printf '%s\n' "${P44_TG_HEALTH:-healthy}" ; exit 0 ;;
      esac
      echo '{}' ; exit 0 ;;
  ecs)
      case "$2" in
        describe-clusters)
            if [ "${P44_PERM_FAIL:-}" = "ecs-clusters" ]; then exit 1; fi
            echo '{"clusters": []}' ; exit 0 ;;
        describe-services)
            if [ "${P44_PERM_FAIL:-}" = "ecs-services" ]; then exit 1; fi
            if [ -n "${P44_SVC_RAW:-}" ]; then printf '%s' "$P44_SVC_RAW"; exit 0; fi
            printf '{"services": [{"desiredCount": %s, "runningCount": %s}]}' \
                   "${P44_DESIRED:-2}" "${P44_RUNNING:-2}"
            exit 0 ;;
      esac
      echo '{}' ; exit 0 ;;
esac
echo '{}'
exit 0
"""

CURL_STUB = r"""#!/bin/sh
echo "curl $*" >> ./markers/curl.calls
printf '%s' "${P44_HTTP_CODE:-200}"
exit 0
"""


def sub_stub(name: str, rc: int = 0) -> str:
    return ('#!/bin/bash\necho "%s invoked" >> ./markers/subscripts.calls\n'
            'echo "[stub] %s"\nexit %d\n' % (name, name, rc))


def controls() -> bool:
    tmp = tempfile.mkdtemp(prefix="p44i_ctl_")
    try:
        ok = []
        p = subprocess.run([BASH, "-c", "echo P44I_OK"], cwd=tmp,
                           capture_output=True, timeout=60)
        ok.append(("shell startup", p.returncode == 0 and b"P44I_OK" in p.stdout))
        p = subprocess.run([BASH, "-c", "exit 31"], cwd=tmp,
                           capture_output=True, timeout=60)
        ok.append(("negative exit", p.returncode == 31))

        # Prove the stub aws shadows the REAL aws CLI that is on PATH.
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "aws"), AWS_STUB, True)
        p = subprocess.run(
            [BASH, "-c", 'export PATH="$PWD/stubbin:$PATH"; command -v aws; aws --version'],
            cwd=tmp, capture_output=True, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        mk = os.path.join(tmp, "markers", "aws.calls")
        marked = os.path.exists(mk) and "aws --version" in open(mk).read()
        ok.append(("stub aws shadows real CLI",
                   "STUB" in out and "stubbin/aws" in out and marked))

        w(os.path.join(tmp, "fail.sh"), "#!/bin/bash\nexit 11\n", True)
        p = subprocess.run([BASH, "-c", "./fail.sh"], cwd=tmp,
                           capture_output=True, timeout=60)
        ok.append(("failing script detected", p.returncode == 11))

        print("ENVIRONMENT CONTROLS")
        print("-" * 104)
        for name, good in ok:
            print("  [%s] %s" % ("x" if good else " ", name))
        allok = all(g for _, g in ok)
        print("  -> controls %s" % ("PASS" if allok else "FAIL"))
        return allok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_case(label: str, scen: dict, acm_rc: int = 0) -> dict:
    tmp = tempfile.mkdtemp(prefix="p44i_dep_")
    try:
        os.makedirs(os.path.join(tmp, "stubbin"))
        os.makedirs(os.path.join(tmp, "markers"))
        w(os.path.join(tmp, "stubbin", "aws"), AWS_STUB, True)
        w(os.path.join(tmp, "stubbin", "curl"), CURL_STUB, True)
        for s in ("acm.sh", "alb.sh", "ecs.sh"):
            w(os.path.join(tmp, s), sub_stub(s, acm_rc if s == "acm.sh" else 0), True)
        w(os.path.join(tmp, "deploy.sh"), committed("infra/deploy.sh"), True)

        # Scenario values are passed through the process environment, NOT through a bash
        # command line. An earlier revision injected `export K=V;` text and a payload
        # containing single quotes broke the quoting, so bash exited 2 without ever
        # starting the script. The stub-marker check detected that; this avoids it.
        env = os.environ.copy()
        env.update({
            "AWS_ACCESS_KEY_ID": "STUB_INVALID",
            "AWS_SECRET_ACCESS_KEY": "STUB_INVALID",
            "AWS_SESSION_TOKEN": "STUB_INVALID",
            "AWS_CONFIG_FILE": os.path.join(tmp, "no_such_config"),
            "AWS_SHARED_CREDENTIALS_FILE": os.path.join(tmp, "no_such_creds"),
            "AWS_EC2_METADATA_DISABLED": "true",
        })
        for k, v in scen.items():
            env[k] = str(v)
        cmd = 'export PATH="$PWD/stubbin:$PATH"; ./deploy.sh'
        try:
            p = subprocess.run([BASH, "-c", cmd], cwd=tmp, env=env,
                               capture_output=True, timeout=TIMEOUT)
            rc = p.returncode
            out = ((p.stdout or b"").decode("utf-8", "replace") +
                   (p.stderr or b"").decode("utf-8", "replace"))
            tmo = False
        except subprocess.TimeoutExpired:
            rc, out, tmo = None, "", True

        def count(f: str) -> int:
            path = os.path.join(tmp, "markers", f)
            if not os.path.exists(path):
                return 0
            return len([l for l in open(path, encoding="utf-8", errors="replace")
                        if l.strip()])

        return {
            "label": label,
            "exit": rc,
            "tmo": tmo,
            "banner": BANNER in out,
            "ready100": READY in out,
            "warn": out.count("[WARN"),
            "fail": out.count("[FAIL]"),
            "aws": count("aws.calls"),
            "subs": count("subscripts.calls"),
            "curl": count("curl.calls"),
            "stub_proven": count("aws.calls") > 0,
            "injected": os.path.exists(os.path.join(tmp, "markers", "injected.marker")),
            "injected_ecs": os.path.exists(
                os.path.join(tmp, "markers", "injected_ecs.marker")),
            "out": out,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    before = sha256_file(REPO_SCRIPT)
    print("=" * 104)
    print("Phase 44 - CALIBRATED harness for infra/deploy.sh (stubs only)")
    print("=" * 104)
    print("bash     : %s" % BASH)
    print("repo sha : %s (before)" % before)
    print("source   : COMMITTED blob (LF). Real aws CLI is on PATH and is SHADOWED.\n")

    if not controls():
        print("\nABORT: environment controls failed; no script result is admissible.")
        return 1

    cases = [
        ("POS all signals healthy",
         dict(P44_ALB_STATE="active", P44_DESIRED=2, P44_RUNNING=2, P44_HTTP_CODE=200), 0),
        ("NEG auth empty",
         dict(P44_AUTH_EMPTY=1), 0),
        ("NEG missing ecs perm",
         dict(P44_PERM_FAIL="ecs-services"), 0),
        ("NEG acm.sh fails",
         dict(P44_ALB_STATE="active", P44_DESIRED=2, P44_RUNNING=2), 1),
        ("wrong AWS account",
         dict(P44_ACCOUNT=999999999999, P44_ALB_STATE="active",
              P44_DESIRED=2, P44_RUNNING=2), 0),
        ("ALL verify signals BAD",
         dict(P44_ALB_STATE="UNKNOWN", P44_DESIRED=4, P44_RUNNING=0,
              P44_HTTP_CODE="000", P44_TG_HEALTH="unhealthy"), 0),
        ("identity parse failure",
         dict(P44_IDENTITY_RAW="not-json-at-all", P44_ALB_STATE="active",
              P44_DESIRED=2, P44_RUNNING=2), 0),
        ("identity python injection",
         {"P44_IDENTITY_RAW": INJECT, "P44_ALB_STATE": "active",
          "P44_DESIRED": 2, "P44_RUNNING": 2}, 0),
        ("ecs metadata has quotes",
         {"P44_SVC_RAW": SVC_QUOTES, "P44_ALB_STATE": "active"}, 0),
        ("ecs payload injection",
         {"P44_SVC_RAW": SVC_INJECT, "P44_ALB_STATE": "active"}, 0),
    ]
    rows = [run_case(*c) for c in cases]

    print("\nSCENARIOS")
    print("-" * 104)
    hdr = ("%-28s %5s %7s %8s %5s %5s %5s %5s %5s %6s %5s" %
           ("case", "exit", "banner", "ready100", "warn", "fail",
            "aws", "subs", "curl", "stubok", "inj"))
    print(hdr)
    for r in rows:
        print("%-28s %5s %7s %8s %5d %5d %5d %5d %5d %6s %5s%s" %
              (r["label"], "TMO" if r["tmo"] else r["exit"], r["banner"], r["ready100"],
               r["warn"], r["fail"], r["aws"], r["subs"], r["curl"],
               r["stub_proven"], r["injected"],
               "" if r["stub_proven"] else "   <- NOT RUN"))

    g = {r["label"]: r for r in rows}
    pos = g["POS all signals healthy"]
    auth = g["NEG auth empty"]
    perm = g["NEG missing ecs perm"]
    acmf = g["NEG acm.sh fails"]
    acct = g["wrong AWS account"]
    bad = g["ALL verify signals BAD"]
    parse = g["identity parse failure"]
    inj = g["identity python injection"]

    print("\nCALIBRATION VERDICT")
    print("-" * 104)
    stub_ok = all(r["stub_proven"] for r in rows)
    pos_ok = pos["exit"] == 0 and pos["banner"] and pos["ready100"] and pos["subs"] == 3
    neg_ok = (auth["exit"] not in (0, None) and not auth["banner"] and auth["subs"] == 0
              and perm["exit"] not in (0, None) and not perm["banner"]
              and perm["subs"] == 0)
    print("  stub invocation proven in ALL cases : %s" % stub_ok)
    print("  POSITIVE control valid              : %s (exit %s, banner %s, 3 sub-scripts)"
          % (pos_ok, pos["exit"], pos["banner"]))
    print("  NEGATIVE controls valid             : %s (auth exit %s, perm exit %s)"
          % (neg_ok, auth["exit"], perm["exit"]))
    print("  CALIBRATED                          : %s"
          % (stub_ok and pos_ok and neg_ok))

    print("\nFINDINGS (only admissible because the controls above pass)")
    print("-" * 104)
    print("  F1 provisioning failure DOES abort        : exit %s, banner %s, subs %d"
          % (acmf["exit"], acmf["banner"], acmf["subs"]))
    print("  F2 wrong-account guard is ADVISORY only   : exit %s, banner %s, ready100 %s"
          % (acct["exit"], acct["banner"], acct["ready100"]))
    print("  F3 ALL FOUR verify signals bad -> success : exit %s, banner %s, ready100 %s, warns %d"
          % (bad["exit"], bad["banner"], bad["ready100"], bad["warn"]))
    print("  F4 identity parse failure fails OPEN      : exit %s, banner %s (degrades to [WARNING])"
          % (parse["exit"], parse["banner"]))
    # A case with zero stub invocations never started the script; its outcome is NOT RUN
    # regardless of the exit code, and must never be read as a negative result.
    print("  F5 AWS output -> Python source injection  : %s"
          % ("NOT RUN (0 stub calls, exit %s)" % inj["exit"] if not inj["stub_proven"]
             else ("marker written %s" % inj["injected"])))
    print("  F6 success output identical good vs bad   : %s"
          % (pos["exit"] == bad["exit"] and pos["banner"] == bad["banner"]
             and pos["ready100"] == bad["ready100"]))

    q = g["ecs metadata has quotes"]
    e = g["ecs payload injection"]
    print("  F7 ecs metadata with ''' -> counts zeroed : %s"
          % ("NOT RUN" if not q["stub_proven"]
             else ("exit %s, banner %s, warns %d" % (q["exit"], q["banner"], q["warn"]))))
    print("  F8 same sink at :115/:116 executes code   : %s"
          % ("NOT RUN" if not e["stub_proven"] else str(e["injected_ecs"])))

    print("\nEVIDENCE EXCERPT - 'ecs metadata has quotes' ECS stability line")
    print("-" * 104)
    for line in q["out"].splitlines():
        if "Tasks Running" in line or "Service Status" in line:
            print("  | %s" % line)

    print("\nEVIDENCE EXCERPT - 'ALL verify signals BAD' tail")
    print("-" * 104)
    for line in [l for l in bad["out"].splitlines() if l.strip()][-12:]:
        print("  | %s" % line)

    after = sha256_file(REPO_SCRIPT)
    print("\nrepo sha : %s (after)" % after)
    print("repository UNCHANGED : %s" % (before == after))
    print("NOT DONE: no real AWS call, no network, no infrastructure change, no repo write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
