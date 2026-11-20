"""Phase 39 PROTOTYPE ONLY — multi-command sequence runner for the blue-green harness.

The headline Phase 38 finding is a *sequence* property: `promote` followed by `rollback`. The
single-command runner resets state per scenario, so it cannot express it. This runner executes
an ordered list of commands against ONE state directory, printing state after each step, so a
transition that destroys recoverability is visible rather than argued.

Requires `staging/p39_harness/` to already exist (built by phase39_build_bluegreen_harness.py).
Writes only inside that directory. PROTOTYPE ONLY — nothing governed is touched.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = ROOT / "staging" / "p39_harness"
SEQ = HARNESS / "seq.sh"

SEQ_RUNNER = r"""#!/usr/bin/env bash
# PHASE 39 sequence runner. Usage: seq.sh <scenario> <initial_active> "cmd1" "cmd2" ...
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="$1"; INITIAL_ACTIVE="$2"; shift 2
export BG_STATE="$HERE/state_$SCENARIO"
rm -rf "$BG_STATE"; mkdir -p "$BG_STATE"

printf '%s' "$INITIAL_ACTIVE"              > "$BG_STATE/active_color"
printf '%s' "registry.example.com/vq:v1"   > "$BG_STATE/trading-backend-blue_image"
printf '%s' "registry.example.com/vq:v2"   > "$BG_STATE/trading-backend-green_image"
printf '%s' "3"                            > "$BG_STATE/trading-backend-blue_replicas"
printf '%s' "3"                            > "$BG_STATE/trading-backend-blue_ready"
printf '%s' "3"                            > "$BG_STATE/trading-backend-green_replicas"
printf '%s' "3"                            > "$BG_STATE/trading-backend-green_ready"
for kv in ${FAULTS:-}; do printf '%s' "${kv#*=}" > "$BG_STATE/${kv%%=*}"; done

export PATH="$HERE/bin:$PATH"
export NAMESPACE=production

echo "SCENARIO: $SCENARIO   (initial active=$INITIAL_ACTIVE)"
echo "BASELINE  active=$(cat "$BG_STATE/active_color") blue=$(cat "$BG_STATE/trading-backend-blue_image") green=$(cat "$BG_STATE/trading-backend-green_image") blue_rep=3 green_rep=3"
echo ""

STEP=0
for CMD in "$@"; do
  STEP=$((STEP+1))
  printf '%s' "0" > "$BG_STATE/health_calls"
  set +e
  bash "$HERE/blue-green-deploy.sh" $CMD > "$BG_STATE/step${STEP}.log" 2>&1
  RC=$?
  set -e
  echo "STEP $STEP: '$CMD'"
  echo "  exit           : $RC"
  echo "  active_color   : $(cat "$BG_STATE/active_color" 2>/dev/null)"
  echo "  blue_image     : $(cat "$BG_STATE/trading-backend-blue_image" 2>/dev/null)"
  echo "  green_image    : $(cat "$BG_STATE/trading-backend-green_image" 2>/dev/null)"
  echo "  blue_replicas  : $(cat "$BG_STATE/trading-backend-blue_replicas" 2>/dev/null)"
  echo "  green_replicas : $(cat "$BG_STATE/trading-backend-green_replicas" 2>/dev/null)"
  echo "  set_image_last : $(cat "$BG_STATE/set_image_last" 2>/dev/null)"
  echo "  key stdout     : $(grep -Ei 'rollback|promot|nothing to|switch|ERROR|SUCCESS' "$BG_STATE/step${STEP}.log" 2>/dev/null | tail -3 | tr '\n' '|')"
  echo ""
done
exit 0
"""


def main() -> int:
    if not HARNESS.exists():
        print("REFUSING: harness missing; run phase39_build_bluegreen_harness.py first")
        return 1
    if SEQ.exists():
        print("REFUSING: %s already exists; not overwriting." % SEQ)
        return 1
    with open(SEQ, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(SEQ_RUNNER)
    subprocess.run(["bash", "-c", "chmod +x staging/p39_harness/seq.sh"], cwd=ROOT)
    r = subprocess.run(["bash", "-n", "staging/p39_harness/seq.sh"],
                       capture_output=True, text=True, cwd=ROOT)
    print("bash -n seq.sh exit=%d %s" % (r.returncode, (r.stderr or "").strip()[:80]))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
