"""Phase 39 PROTOTYPE ONLY — stateful blue-green lifecycle harness.

WHAT THIS IS
------------
Phase 38 traced `scripts/blue-green-deploy.sh` by reading it and reported three lifecycle
defects. Reading is not reproduction. This builds an isolated harness that EXECUTES the real
production script against a `kubectl` stub which actually MUTATES the state it claims to
mutate, then asserts on exit status AND final modeled state.

The prompt's §5.5 warning is the design constraint: "a stateful stub must update the state it
claims to mutate. A harness that returns the same old state after a successful patch is invalid
evidence." So the stub persists deployment images, replica counts, ready counts and the active
service selector to a state directory, and `set image` / `scale` / `patch` write to it.

WHY A SHELL STUB AND NOT A PYTHON REIMPLEMENTATION
--------------------------------------------------
Reimplementing the lifecycle in Python and testing that would be self-referential — the exact
defect class Phase 38 found in `phase18_test_release_gate.py`. This runs the committed bytes of
the real script with `kubectl` shadowed on PATH, so the control flow under test is production's.

SAFETY
------
* `kubectl` is ABSENT from this machine, so the stub is the only `kubectl` on PATH.
* The stub never performs network or cluster I/O. It reads and writes one temp state dir.
* The script is run from a temp copy of the COMMITTED blob (LF), never the working tree.
* Everything lives under `staging/p39_harness/` and is removed by the caller.

This file generates the harness with explicit LF newlines, because CRLF would break bash —
which is the very portability defect Phase 38 documented.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = ROOT / "staging" / "p39_harness"


def w(path: pathlib.Path, text: str) -> None:
    """Write with LF endings unconditionally."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(text)


KUBECTL_STUB = r"""#!/usr/bin/env bash
# PHASE 39 STUB. Not kubectl. Models cluster state in $BG_STATE as flat key=value files.
# Every mutating verb writes state; every read verb reads it. No network, no cluster.
set -u
S="${BG_STATE:?BG_STATE must be set}"
log() { echo "[stub] $*" >> "$S/calls.log"; }
get() { cat "$S/$1" 2>/dev/null || echo ""; }
put() { printf '%s' "$2" > "$S/$1"; }

log "$*"
VERB="${1:-}"; shift || true

# Resolve a deployment name out of the remaining args, tolerating `deployment/x` and `deploy/x`.
dep_of() {
  for a in "$@"; do
    case "$a" in
      deployment/*|deploy/*) echo "${a#*/}"; return;;
      trading-backend-blue|trading-backend-green) echo "$a"; return;;
    esac
  done
  echo ""
}

case "$VERB" in
  get)
    KIND="${1:-}"; shift || true
    NAME="$(dep_of "$@")"
    [ -z "$NAME" ] && NAME="${1:-}"
    case "$KIND" in
      service)
        # Only the active-selector jsonpath is used by the script.
        if [ "$(get svc_get_fails)" = "1" ]; then exit 1; fi
        echo "$(get active_color)"
        ;;
      deployment|deployments|deploy)
        for a in "$@"; do
          case "$a" in
            *'spec.template.spec.containers[0].image'*)
              if [ "$NAME" = "trading-backend-green" ] && [ "$(get green_image_get_fails)" = "1" ]; then exit 1; fi
              echo "$(get ${NAME}_image)"; exit 0;;
            *'spec.replicas'*) echo "$(get ${NAME}_replicas)"; exit 0;;
            *'status.readyReplicas'*) echo "$(get ${NAME}_ready)"; exit 0;;
          esac
        done
        echo ""
        ;;
      pods) echo "NAME READY STATUS"; ;;
      namespace) exit 0 ;;
      *) echo "" ;;
    esac
    ;;
  set)
    # set image deployment/<name> backend=<image> -n ns
    NAME="$(dep_of "$@")"
    IMG=""
    for a in "$@"; do case "$a" in backend=*) IMG="${a#backend=}";; esac; done
    if [ -z "$NAME" ]; then echo "stub: no deployment" >&2; exit 1; fi
    put "${NAME}_image" "$IMG"
    put "set_image_last" "$NAME=$IMG"
    echo "deployment.apps/$NAME image updated"
    ;;
  scale)
    NAME="$(dep_of "$@")"
    REP=""
    for a in "$@"; do case "$a" in --replicas=*) REP="${a#--replicas=}";; esac; done
    put "${NAME}_replicas" "$REP"
    put "${NAME}_ready" "$REP"
    echo "deployment.apps/$NAME scaled"
    ;;
  patch)
    # patch service <name> --type=json -p=[{... "value": "green"}]
    if [ "$(get patch_fails)" = "1" ]; then echo "stub: patch denied" >&2; exit 1; fi
    NEW=""
    for a in "$@"; do
      case "$a" in
        *'"value": "green"'*|*'"value":"green"'*) NEW="green";;
        *'"value": "blue"'*|*'"value":"blue"'*) NEW="blue";;
      esac
    done
    if [ -n "$NEW" ]; then
      if [ "$(get patch_silently_ignored)" = "1" ]; then
        log "patch accepted but selector intentionally NOT advanced"
      else
        put active_color "$NEW"
      fi
    fi
    echo "service/trading-backend-active patched"
    ;;
  rollout)
    NAME="$(dep_of "$@")"
    if [ "$(get rollout_fails)" = "1" ]; then echo "stub: rollout did not complete" >&2; exit 1; fi
    echo "deployment \"$NAME\" successfully rolled out"
    ;;
  wait)
    if [ "$(get wait_fails)" = "1" ]; then echo "stub: pods never ready" >&2; exit 1; fi
    echo "pod/green-abc condition met"
    ;;
  exec)
    # The script probes /health then /health/ready via `curl -sf` inside the pod.
    N="$(get health_calls)"; [ -z "$N" ] && N=0; N=$((N+1)); put health_calls "$N"
    FAILAT="$(get health_fail_at)"
    if [ -n "$FAILAT" ] && [ "$N" = "$FAILAT" ]; then exit 1; fi
    if [ "$(get health_always_fails)" = "1" ]; then exit 1; fi
    exit 0
    ;;
  annotate|label) echo "ok" ;;
  cluster-info) if [ "$(get cluster_down)" = "1" ]; then exit 1; fi; echo "stub cluster" ;;
  *) echo "stub: unhandled verb '$VERB'" >&2; exit 0 ;;
esac
exit 0
"""

RUNNER = r"""#!/usr/bin/env bash
# PHASE 39 scenario runner. Usage: run.sh <scenario> <command...>
# Prepares a fresh state dir, shadows kubectl, runs the real script, prints exit + final state.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="$1"; shift
export BG_STATE="$HERE/state_$SCENARIO"
rm -rf "$BG_STATE"; mkdir -p "$BG_STATE"

# Baseline cluster: blue is active and serving v1; green is idle at 0.
printf '%s' "blue"                         > "$BG_STATE/active_color"
printf '%s' "registry.example.com/vq:v1"   > "$BG_STATE/trading-backend-blue_image"
printf '%s' "registry.example.com/vq:v0"   > "$BG_STATE/trading-backend-green_image"
printf '%s' "3"                            > "$BG_STATE/trading-backend-blue_replicas"
printf '%s' "3"                            > "$BG_STATE/trading-backend-blue_ready"
printf '%s' "0"                            > "$BG_STATE/trading-backend-green_replicas"
printf '%s' "0"                            > "$BG_STATE/trading-backend-green_ready"

# Scenario-specific fault injection, written before the script runs.
for kv in ${FAULTS:-}; do printf '%s' "${kv#*=}" > "$BG_STATE/${kv%%=*}"; done

export PATH="$HERE/bin:$PATH"
export NAMESPACE=production
set +e
bash "$HERE/blue-green-deploy.sh" "$@" > "$BG_STATE/stdout.log" 2>&1
RC=$?
set -e

echo "SCENARIO      : $SCENARIO"
echo "COMMAND       : $*"
echo "EXIT          : $RC"
echo "active_color  : $(cat "$BG_STATE/active_color" 2>/dev/null)"
echo "blue_image    : $(cat "$BG_STATE/trading-backend-blue_image" 2>/dev/null)"
echo "green_image   : $(cat "$BG_STATE/trading-backend-green_image" 2>/dev/null)"
echo "blue_replicas : $(cat "$BG_STATE/trading-backend-blue_replicas" 2>/dev/null)"
echo "green_replicas: $(cat "$BG_STATE/trading-backend-green_replicas" 2>/dev/null)"
echo "health_calls  : $(cat "$BG_STATE/health_calls" 2>/dev/null)"
echo "set_image_last: $(cat "$BG_STATE/set_image_last" 2>/dev/null)"
exit 0
"""


def main() -> int:
    if HARNESS.exists():
        print("REFUSING: %s already exists; not overwriting." % HARNESS)
        return 1

    # The script under test is the COMMITTED blob (LF), never the CRLF working tree.
    blob = subprocess.run(
        ["git", "show", "HEAD:scripts/blue-green-deploy.sh"],
        capture_output=True, cwd=ROOT,
    ).stdout
    if not blob:
        print("REFUSING: could not read committed blob")
        return 1

    (HARNESS / "bin").mkdir(parents=True, exist_ok=True)
    (HARNESS / "blue-green-deploy.sh").write_bytes(blob)
    w(HARNESS / "bin" / "kubectl", KUBECTL_STUB)
    w(HARNESS / "run.sh", RUNNER)
    subprocess.run(["bash", "-c", "chmod +x '%s' '%s'" % (
        "staging/p39_harness/bin/kubectl", "staging/p39_harness/run.sh")], cwd=ROOT)

    # Prove the script and stub both parse before drawing any conclusion from behaviour.
    for target in ("staging/p39_harness/blue-green-deploy.sh",
                   "staging/p39_harness/bin/kubectl",
                   "staging/p39_harness/run.sh"):
        r = subprocess.run(["bash", "-n", target], capture_output=True, text=True, cwd=ROOT)
        print("bash -n %-48s exit=%d %s" % (target, r.returncode, (r.stderr or "").strip()[:60]))
        if r.returncode != 0:
            return 1
    print("harness built at staging/p39_harness (PROTOTYPE ONLY)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
