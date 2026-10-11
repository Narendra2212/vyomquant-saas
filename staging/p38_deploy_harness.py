#!/usr/bin/env python3
"""
PHASE 38 / PROTOTYPE ONLY -- deployment-script failure-semantics harness.

Reproduces (or refutes) the Phase 32 claim that scripts/deploy.sh can exit 0
and print a success banner even when every health check fails.

ENVIRONMENT NOTE
  `bash` on this host is WSL2 (Linux kernel, /mnt/c mount of the repo). An
  earlier revision of this harness drove bash with Windows paths and `chmod`
  on a drvfs mount; every scenario returned exit 1 with no output because the
  interpreter never started. That was a harness defect, not a finding, and the
  result was discarded. This revision does all of its work inside the WSL
  native filesystem (/tmp), where exec permissions are real.

SAFETY PROPERTIES
  * Never modifies scripts/deploy.sh or any other repository file.
  * Reads the script from the COMMITTED BLOB (`git show HEAD:...`), so the
    local core.autocrlf=true working-tree CRLF conversion cannot affect it.
  * Runs entirely inside a throwaway /tmp directory, removed afterwards.
  * kubectl / curl / sleep are stubs on PATH. No cluster, no network, no AWS,
    no real deployment, no real 10s sleeps.

Usage: python staging/p38_deploy_harness.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_WIN = Path(__file__).resolve().parent.parent
SCRIPT_IN_REPO = "scripts/deploy.sh"
OUT = REPO_WIN / "staging" / "p38_deploy_harness.txt"

# Each scenario: id, label, and the env/arg knobs the stubs read.
SCENARIOS = [
    ("A", "all health checks succeed",            "STUB_CURL_FAIL_COUNT=0",                               "staging"),
    ("B", "ALL 5 health checks fail",             "STUB_CURL_FAIL_COUNT=-1",                              "staging"),
    ("C", "4 fail then the 5th succeeds",         "STUB_CURL_FAIL_COUNT=4",                               "staging"),
    ("D", "curl absent from PATH",                "OMIT_CURL=1",                                          "staging"),
    ("E", ".env.staging missing",                 "OMIT_ENV=1 STUB_CURL_FAIL_COUNT=0",                    "staging"),
    ("F", ".env w/ quoted values + spaces",       "HOSTILE_ENV=1 STUB_CURL_FAIL_COUNT=0",                 "staging"),
    ("N", ".env w/ $(...) substitution probe",    "HOSTILE_ENV=2 STUB_CURL_FAIL_COUNT=0",                 "staging"),
    ("G", "kubectl cluster-info fails",           "STUB_KUBECTL_CLUSTERINFO_RC=1",                        "staging"),
    ("H", "namespace missing",                    "STUB_KUBECTL_NAMESPACE_RC=1",                          "staging"),
    ("I", "kubectl set image fails",              "STUB_KUBECTL_SET_RC=1 STUB_CURL_FAIL_COUNT=0",         "staging"),
    ("J", "kubectl rollout status fails",         "STUB_KUBECTL_ROLLOUT_RC=1 STUB_CURL_FAIL_COUNT=0",     "staging"),
    ("K", "no image tag supplied (default)",      "STUB_CURL_FAIL_COUNT=0",                               "staging"),
    ("L", "genuinely empty image tag",            "STUB_CURL_FAIL_COUNT=0",                               'staging ""'),
    ("O", "junk image tag (not a digest)",        "STUB_CURL_FAIL_COUNT=0",                               'staging "not-a-digest!!"'),
    ("M", "invalid environment name",             "STUB_CURL_FAIL_COUNT=0",                               "prod-oops"),
]

DRIVER = r"""
set -u
REPO="$1"
WORK="$(mktemp -d /tmp/p38_deploy_XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---- the script under test: committed bytes, not the working tree ----------
cd "$REPO" || { echo "FATAL cannot cd repo"; exit 90; }
git show "HEAD:__SCRIPT__" > "$WORK/deploy_blob.sh" || { echo "FATAL git show"; exit 91; }
BLOB_BYTES=$(wc -c < "$WORK/deploy_blob.sh")
BLOB_CRLF=$(grep -c $'\r' "$WORK/deploy_blob.sh" || true)
echo "META|blob_bytes=$BLOB_BYTES|blob_cr_lines=$BLOB_CRLF"

run_one() {
  local id="$1"; shift
  local knobs="$1"; shift
  local sargs="$1"; shift

  local S="$WORK/s_$id"
  rm -rf "$S"; mkdir -p "$S/bin"
  cp "$WORK/deploy_blob.sh" "$S/deploy.sh"
  chmod +x "$S/deploy.sh"

  cat > "$S/bin/kubectl" <<'EOS'
#!/bin/bash
echo "[kubectl] $*" >> "$HARNESS_LOG"
case "$1" in
  cluster-info) exit "${STUB_KUBECTL_CLUSTERINFO_RC:-0}" ;;
  get) if [ "${2:-}" = "namespace" ]; then exit "${STUB_KUBECTL_NAMESPACE_RC:-0}"; fi
       exit "${STUB_KUBECTL_GET_RC:-0}" ;;
  set) echo "SETIMAGE: $*" >> "$HARNESS_IMAGE_LOG"
       exit "${STUB_KUBECTL_SET_RC:-0}" ;;
  rollout) exit "${STUB_KUBECTL_ROLLOUT_RC:-0}" ;;
esac
exit 0
EOS

  cat > "$S/bin/curl" <<'EOS'
#!/bin/bash
echo "[curl] $*" >> "$HARNESS_LOG"
CF="$HARNESS_SBOX/curl_calls"
n=0; [ -f "$CF" ] && n=$(cat "$CF")
n=$((n+1)); echo "$n" > "$CF"
fc="${STUB_CURL_FAIL_COUNT:-0}"
[ "$fc" = "-1" ] && exit 22
[ "$n" -le "$fc" ] && exit 22
exit 0
EOS

  cat > "$S/bin/sleep" <<'EOS'
#!/bin/bash
echo "[sleep] $*" >> "$HARNESS_LOG"
exit 0
EOS
  chmod +x "$S/bin/kubectl" "$S/bin/curl" "$S/bin/sleep"

  # knob parsing
  local omit_curl=0 omit_env=0 hostile=0
  case " $knobs " in *" OMIT_CURL=1 "*) omit_curl=1 ;; esac
  case " $knobs " in *" OMIT_ENV=1 "*) omit_env=1 ;; esac
  case " $knobs " in *" HOSTILE_ENV=1 "*) hostile=1 ;; esac
  case " $knobs " in *" HOSTILE_ENV=2 "*) hostile=2 ;; esac
  [ "$omit_curl" = 1 ] && rm -f "$S/bin/curl"

  if [ "$omit_env" = 0 ]; then
    if [ "$hostile" = 2 ]; then
      # Substitution probe. The marker path is RELATIVE: deploy.sh runs with
      # cwd=$S, so if this ever executes the file lands where we check for it.
      cat > "$S/.env.staging" <<'EOS'
API_URL=https://staging.example.com
SUBST=$(touch SUBST_EXECUTED)
EOS
    elif [ "$hostile" = 1 ]; then
      # Ordinary, well-formed dotenv values: a comment, a blank line, and two
      # quoted values containing spaces. Nothing hostile, nothing exotic.
      cat > "$S/.env.staging" <<'EOS'
# comment line
API_URL=https://staging.example.com

QUOTED="a value with spaces"
SINGLE='another value'
EOS
    else
      cat > "$S/.env.staging" <<'EOS'
# staging environment
API_URL=https://staging.example.com
WORKERS=4
EOS
    fi
  fi

  touch "$S/calls.log" "$S/image.log"

  # Build argv EXACTLY as written, so an empty argument stays empty instead of
  # degrading into the literal two-character token "" via word splitting.
  local ARGS=()
  eval "ARGS=($sargs)"

  # Run with a minimal PATH: stubs first, then real coreutils.
  set +e
  ( cd "$S" && env -i \
      PATH="$S/bin:/usr/bin:/bin" \
      HOME="$S" \
      HARNESS_SBOX="$S" \
      HARNESS_LOG="$S/calls.log" \
      HARNESS_IMAGE_LOG="$S/image.log" \
      $knobs \
      bash ./deploy.sh "${ARGS[@]+"${ARGS[@]}"}" ) > "$S/out.txt" 2>&1
  local rc=$?
  set -e

  local complete=0 healthy=0
  grep -q 'complete!'            "$S/out.txt" && complete=1
  grep -q 'Health check passed'  "$S/out.txt" && healthy=1
  local img; img=$(tr '\n' ' ' < "$S/image.log" | sed 's/  */ /g')

  # Did a $(...) inside .env ACTUALLY execute? Decided by the existence of a
  # marker file the substitution would create -- never by grepping stdout,
  # because `export` echoes the offending token back in its own error message
  # and a stdout grep therefore reports evaluation that did not happen.
  local inj=0
  [ -f "$S/SUBST_EXECUTED" ] && inj=1

  echo "RESULT|$id|$rc|$complete|$healthy|$inj|${img}"
  echo "----OUT $id----"
  sed 's/^/    /' "$S/out.txt"
  echo "----END $id----"
}
"""


def _emit(lines: list[str]) -> None:
    """Write UTF-8 to the artifact, then echo an ASCII-safe copy to stdout.

    deploy.sh prints emoji; this host's console codec is cp1252, so a direct
    stdout write of the captured output raises UnicodeEncodeError. The file is
    written first so a console-encoding problem can never lose the evidence.
    """
    text = "\n".join(lines) + "\n"
    # The Windows volume can be full. Never let an artifact write failure
    # destroy the evidence: print first, persist best-effort.
    sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
    try:
        with open(OUT, "w", newline="\n", encoding="utf-8") as fh:
            fh.write(text)
        size = OUT.stat().st_size
        sys.stdout.write("\n[artifact] %s  %d bytes\n" % (OUT.name, size))
        if size == 0:
            sys.stdout.write("[artifact] ZERO BYTES -- disk full; "
                             "stdout above is the only valid record.\n")
    except OSError as exc:
        sys.stdout.write("\n[artifact] write FAILED (%s); "
                         "stdout above is the only record.\n" % exc)


def main() -> int:
    script_rel = SCRIPT_IN_REPO
    driver = DRIVER.replace("__SCRIPT__", script_rel)
    calls = "\n".join(
        f'run_one "{sid}" "{knobs}" \'{sargs}\''
        for sid, _label, knobs, sargs in SCENARIOS
    )
    driver = driver + "\n" + calls + "\n"

    # Translate the Windows repo path into its WSL mount point.
    wslpath = subprocess.run(
        ["bash", "-lc", f"wslpath -a '{REPO_WIN}'"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    repo_wsl = wslpath.stdout.decode().strip()
    if not repo_wsl:
        print("FATAL: could not translate repo path via wslpath", file=sys.stderr)
        return 2

    proc = subprocess.run(
        ["bash", "-s", repo_wsl],
        input=driver.encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=600,
    )
    raw = proc.stdout.decode("utf-8", "replace")

    labels = {sid: label for sid, label, _k, _a in SCENARIOS}
    args_of = {sid: sargs for sid, _l, _k, sargs in SCENARIOS}
    results: dict[str, dict] = {}
    meta = {}
    for line in raw.splitlines():
        if line.startswith("META|"):
            for kv in line.split("|")[1:]:
                k, _, v = kv.partition("=")
                meta[k] = v
        elif line.startswith("RESULT|"):
            parts = line.split("|")
            sid, rc, comp, heal, inj = parts[1], int(parts[2]), parts[3], parts[4], parts[5]
            img = parts[6] if len(parts) > 6 else ""
            results[sid] = {
                "exit": rc,
                "complete": comp == "1",
                "healthy": heal == "1",
                "injected": inj == "1",
                "image": img.strip(),
            }

    L: list[str] = []
    w = L.append
    w("PHASE 38 -- scripts/deploy.sh failure-semantics harness   [PROTOTYPE ONLY]")
    w("")
    w(f"  script under test : HEAD:{script_rel}  (committed blob, not worktree)")
    w(f"  blob bytes        : {meta.get('blob_bytes','?')}")
    w(f"  blob lines with CR: {meta.get('blob_cr_lines','?')}   (0 => LF-only, per Phase 35)")
    w(f"  bash              : WSL2, repo seen at {repo_wsl}")
    w("  sandbox           : /tmp inside WSL (native fs, real exec bits)")
    w("  kubectl/curl/sleep: STUBS. No cluster, no network, no AWS, no real sleep.")
    w("")

    if not results:
        w("  HARNESS FAILED -- no scenario produced a RESULT line. Raw output follows.")
        w("")
        for ln in raw.splitlines():
            w("    " + ln)
        _emit(L)
        return 3

    w("=" * 94)
    w("SCENARIO RESULTS")
    w("=" * 94)
    w("")
    w(f"  {'id':<3}{'scenario':<38}{'args':<14}{'exit':>5}{'complete?':>11}{'healthy?':>10}")
    w(f"  {'-'*3}{'-'*38}{'-'*14}{'-'*5}{'-'*11}{'-'*10}")
    for sid, label, _k, _a in SCENARIOS:
        r = results.get(sid)
        if not r:
            w(f"  {sid:<3}{label:<38}{args_of[sid]:<14}{'--':>5}{'NO RESULT':>11}")
            continue
        w(f"  {sid:<3}{label:<38}{args_of[sid]:<14}{r['exit']:>5}"
          f"{str(r['complete']):>11}{str(r['healthy']):>10}")
    w("")

    w("=" * 94)
    w("FINDINGS")
    w("=" * 94)
    w("")

    b = results.get("B")
    if b and b["exit"] == 0 and b["complete"] and not b["healthy"]:
        w("  D32-1  CONFIRMED -- EXECUTED REPRODUCTION")
        w("     All 5 health checks failed. deploy.sh exited 0 and printed the success")
        w("     banner, and never printed 'Health check passed'.")
    elif b:
        w(f"  D32-1  NOT REPRODUCED AS STATED -- exit={b['exit']} "
          f"complete={b['complete']} healthy={b['healthy']}")
    w("     Source basis: the retry loop at :61-68")
    w("       for i in {1..5}; do if curl -sf $HEALTH_URL >/dev/null; then ...; break; fi")
    w("       echo attempt; sleep 10; done")
    w("     has no post-exhaustion branch, and `set -e` cannot fire because curl's")
    w("     nonzero status is consumed by the `if` condition.")
    w("")

    e = results.get("E")
    if e:
        verdict = ("CONFIRMED -- EXECUTED REPRODUCTION" if e["exit"] == 0
                   else f"NOT REPRODUCED (exit={e['exit']})")
        w(f"  D32-2  missing .env.staging: {verdict}")
        w(f"     exit={e['exit']} complete={e['complete']}")
        w("     Source basis :21  export $(grep -v '^#' .env.$ENVIRONMENT | xargs)")
        w("     grep's failure is confined to a command substitution; `export` with no")
        w("     operands returns 0, so `set -e` observes success.")
    w("")

    d = results.get("D")
    if d:
        w(f"  D38-1  curl absent from PATH: exit={d['exit']} complete={d['complete']}")
        w("     A missing health-check client and a failing health check are")
        w("     indistinguishable: both traverse the same unguarded loop.")
    w("")

    w("  D38-2  image identity actually handed to Kubernetes")
    for sid, desc in (("K", "no tag argument      "),
                      ("L", "empty tag argument   "),
                      ("O", "junk tag argument    ")):
        r = results.get(sid)
        if r:
            w(f"     {desc} -> exit={r['exit']}  {r['image'] or '(no set-image call)'}")
    la = results.get("L")
    if la and "backend=latest" in la["image"]:
        w("     An EXPLICITLY EMPTY tag is silently promoted to `latest`:")
        w("     `IMAGE_TAG=${2:-latest}` at :9 uses the default for unset AND null,")
        w("     so passing '' does not fail -- it selects the mutable tag.")
    o = results.get("O")
    if o and o["exit"] == 0:
        w("     A syntactically invalid tag is accepted and deployed: there is no")
        w("     digest validation, no tag-shape check, and no rejection path.")
    w("     :46-47 `backend=$IMAGE_TAG` is unquoted and never digest-validated.")
    w("")

    f = results.get("F")
    if f:
        verdict = ("FAILS the deployment" if f["exit"] != 0
                   else "tolerated (exit 0)")
        w(f"  D38-3  .env with ordinary quoted values containing spaces: "
          f"exit={f['exit']} -- {verdict}")
        w("     :21 `export $(grep -v '^#' .env.$ENVIRONMENT | xargs)` word-splits the")
        w("     substitution result, so `QUOTED=\"a value with spaces\"` becomes several")
        w("     words and `export` rejects the ones that are not identifiers. This is")
        w("     an availability defect on well-formed configuration, not a security one.")
    n = results.get("N")
    if n:
        w(f"  D38-4  .env containing $(...): exit={n['exit']}  "
          f"substitution ACTUALLY executed = {n['injected']}")
        if not n["injected"]:
            w("     NOT an injection. The marker file was never created, so the")
            w("     substitution did not run. `export $(...)` applies word splitting and")
            w("     globbing to the result but does NOT re-run command substitution.")
            w("     An earlier revision of this harness grepped stdout for the marker")
            w("     and reported evaluation; that matched `export`'s own error message")
            w("     echoing the token back. That reading is WITHDRAWN.")
        else:
            w("     Substitution executed -- this IS an injection path.")
    w("")

    w("  POSITIVE CONTROLS (must be nonzero, else the harness cannot see failure)")
    for sid in ("G", "H", "I", "J", "M"):
        r = results.get(sid)
        if not r:
            continue
        ok = "OK  nonzero" if r["exit"] != 0 else "*** EXITS 0 ***"
        w(f"     {sid}  {labels[sid]:<34} exit={r['exit']:<4}{ok}")
    w("")
    nonzero = [s for s in ("G", "H", "I", "J", "M")
               if results.get(s) and results[s]["exit"] != 0]
    w(f"     {len(nonzero)}/5 controls returned nonzero.")
    if not nonzero:
        w("     WARNING: no control failed. Treat every exit-0 above as UNRELIABLE.")
    w("")
    w("  NO REPOSITORY FILE WAS MODIFIED. NO CLUSTER WAS CONTACTED.")
    w("")
    w("=" * 94)
    w("RAW SCENARIO OUTPUT")
    w("=" * 94)
    for ln in raw.splitlines():
        if ln.startswith("RESULT|") or ln.startswith("META|"):
            continue
        w(ln)

    _emit(L)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
