#!/bin/bash
# PHASE 34 — prove the blue-green harness can DISTINGUISH a safe implementation from the
# current unsafe one. Phase 33's harness lacked this, so its passes had no demonstrated
# discriminating power.
#
# Method: run the same scenario against (A) the real script and (B) a PATCHED COPY that lives
# only in a temp directory. The repository file is never modified. If the harness reports the
# same verdict for both, it cannot detect the fix and its evidence is worthless.
#
# Scenario under test: `switch` invoked standalone. Safe behaviour = refuse or validate before
# patching the production traffic selector. Unsafe = patch with zero health probes, exit 0.
#
# No cluster, no network, no cloud. kubectl/curl/sleep are stubs.

set -u
REPO_ROOT="${1:?usage: phase34_bluegreen_negative_control.sh <repo-root>}"
SUT="$REPO_ROOT/scripts/blue-green-deploy.sh"
[ -f "$SUT" ] || { echo "BLOCKED: $SUT not found"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/stubs" "$WORK/a" "$WORK/b"

tr -d '\r' < "$SUT" > "$WORK/a/bg.sh"          # A: unmodified
tr -d '\r' < "$SUT" > "$WORK/b/bg.sh"          # B: about to be patched
chmod +x "$WORK/a/bg.sh" "$WORK/b/bg.sh"

# ── Build variant B: insert a pre-switch validation call into switch_traffic ───────────
# The minimal shape of the fix: validate the candidate BEFORE patching the selector. This is
# a DEMONSTRATION of the fix for harness calibration, NOT an installed change.
python3 - "$WORK/b/bg.sh" <<'PY'
import re, sys
p = sys.argv[1]
src = open(p, encoding='utf-8').read()
# Place the validation immediately before the first selector patch inside switch_traffic.
needle = '    log_info "Switching traffic to GREEN..."'
fix = ('    # PHASE34 DEMO FIX: a candidate must be validated before it can receive traffic.\n'
       '    validate_green || { log_error "Green failed validation; refusing to switch."; exit 1; }\n')
assert needle in src, "anchor not found; the script changed shape"
src = src.replace(needle, needle + "\n" + fix, 1)
open(p, 'w', encoding='utf-8').write(src)
print("variant B patched: validate_green inserted before the selector patch")
PY

CALLS="$WORK/calls.log"
STATE="$WORK/state"

cat > "$WORK/stubs/kubectl" <<'STUB'
#!/bin/bash
echo "kubectl $*" >> "$CALLS"
A="$*"
case "$A" in
  *'"value": "green"'*) echo green > "$STUB_STATE_FILE"; exit 0 ;;
  *'"value": "blue"'*)  echo blue  > "$STUB_STATE_FILE"; exit 0 ;;
esac
case "$A" in
  *"jsonpath={.spec.selector.color}"*)
      if [ -s "$STUB_STATE_FILE" ]; then cat "$STUB_STATE_FILE"; else echo blue; fi; exit 0 ;;
  *"jsonpath={.spec.template.spec.containers[0].image}"*) echo "repo/app:old"; exit 0 ;;
  *"jsonpath={.spec.replicas}"*)     echo "${STUB_REPLICAS:-3}"; exit 0 ;;
  *"jsonpath={.status.readyReplicas}"*) echo "${STUB_READY:-3}"; exit 0 ;;
  *"/health/ready"*) exit "${STUB_READY_EXIT:-0}" ;;
  *"/health"*)       exit "${STUB_HEALTH_EXIT:-0}" ;;
  *) exit 0 ;;
esac
STUB
printf '#!/bin/bash\necho "curl $*" >> "$CALLS"\nexit 0\n' > "$WORK/stubs/curl"
printf '#!/bin/bash\nexit 0\n' > "$WORK/stubs/sleep"
chmod +x "$WORK/stubs/"*

export STUB_STATE_FILE="$STATE"

probe() {  # variant-dir, health-exit  -> "exit,probes,patches"
  : > "$CALLS"; : > "$STATE"
  ( cd "$1" && CALLS="$CALLS" STUB_STATE_FILE="$STATE" STUB_HEALTH_EXIT="$2" \
      PATH="$WORK/stubs:$PATH" ./bg.sh switch >"$WORK/out.txt" 2>&1 )
  local rc=$?
  local probes=$(grep -c 'health' "$CALLS" || true)
  local patches=$(grep -c 'patch service' "$CALLS" || true)
  echo "$rc,$probes,$patches"
}

echo "=== blue-green harness NEGATIVE CONTROL ==="
echo "real script sha1: $(sha1sum "$SUT" | cut -d' ' -f1)"
echo ""
printf '  %-44s %-8s %-8s %s\n' "variant / condition" "exit" "probes" "selector patches"
printf '  %s\n' "----------------------------------------------------------------------------"

IFS=, read -r A_RC A_PROBES A_PATCH <<< "$(probe "$WORK/a" 0)"
printf '  %-44s %-8s %-8s %s\n' "A real script, green healthy" "$A_RC" "$A_PROBES" "$A_PATCH"

IFS=, read -r A2_RC A2_PROBES A2_PATCH <<< "$(probe "$WORK/a" 1)"
printf '  %-44s %-8s %-8s %s\n' "A real script, green UNHEALTHY" "$A2_RC" "$A2_PROBES" "$A2_PATCH"

IFS=, read -r B_RC B_PROBES B_PATCH <<< "$(probe "$WORK/b" 0)"
printf '  %-44s %-8s %-8s %s\n' "B demo-fixed script, green healthy" "$B_RC" "$B_PROBES" "$B_PATCH"

IFS=, read -r B2_RC B2_PROBES B2_PATCH <<< "$(probe "$WORK/b" 1)"
printf '  %-44s %-8s %-8s %s\n' "B demo-fixed script, green UNHEALTHY" "$B2_RC" "$B2_PROBES" "$B2_PATCH"

echo ""
echo "  INTERPRETATION"
UNSAFE=0; SAFE=0
# Real script must switch an UNHEALTHY candidate: exit 0 and a patch issued.
if [ "$A2_RC" = "0" ] && [ "$A2_PATCH" -ge 1 ] && [ "$A2_PROBES" = "0" ]; then
  echo "    A (real)  UNSAFE confirmed: unhealthy candidate received traffic, exit 0, 0 probes"
  UNSAFE=1
else
  echo "    A (real)  did NOT reproduce the unsafe path (exit=$A2_RC probes=$A2_PROBES patches=$A2_PATCH)"
fi
# Demo-fixed script must refuse the unhealthy candidate with no patch.
if [ "$B2_RC" != "0" ] && [ "$B2_PATCH" = "0" ]; then
  echo "    B (fixed) SAFE confirmed: refused before patching, exit $B2_RC, 0 selector patches"
  SAFE=1
else
  echo "    B (fixed) did NOT behave safely (exit=$B2_RC probes=$B2_PROBES patches=$B2_PATCH)"
fi
# And the fixed script must still allow a healthy switch.
if [ "$B_RC" = "0" ] && [ "$B_PATCH" -ge 1 ]; then
  echo "    B (fixed) still permits a HEALTHY switch, so the fix is not merely a hard block"
else
  echo "    B (fixed) blocked a healthy switch too — the demo fix is too strict"
fi

echo ""
if [ "$UNSAFE" = "1" ] && [ "$SAFE" = "1" ]; then
  echo "  HARNESS VALID: it distinguishes the unsafe real script from a safe variant."
  exit 0
fi
echo "  HARNESS INVALID: it cannot tell the two implementations apart. Phase 33's"
echo "  blue-green passes must be treated as unproven until this is corrected."
exit 1
