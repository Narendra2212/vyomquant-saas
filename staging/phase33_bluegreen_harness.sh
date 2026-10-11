#!/bin/bash
# PHASE 33 harness — exercise scripts/blue-green-deploy.sh promotion/rollback branches.
#
# PROTOTYPE ONLY. Reads the script; never modifies it. No cluster, no network, no cloud:
# `kubectl`, `curl` and `sleep` are stubs on PATH and the script runs from a temp directory.
# The stub kubectl answers from env vars and appends every invocation to a call log, so the
# assertions are about observed commands rather than inferred behaviour.
#
# CRLF note: this Windows worktree has core.autocrlf=true; the COMMITTED blob is pure LF
# (verified git cat-file blob -> CR=0), so `tr -d` here removes a local checkout artifact and
# not a repository defect.
#
# Usage: bash staging/phase33_bluegreen_harness.sh <repo-root>

set -u

REPO_ROOT="${1:?usage: phase33_bluegreen_harness.sh <repo-root>}"
SUT="$REPO_ROOT/scripts/blue-green-deploy.sh"
[ -f "$SUT" ] || { echo "BLOCKED: $SUT not found"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/stubs" "$WORK/run"
tr -d '\r' < "$SUT" > "$WORK/run/bg.sh"
chmod +x "$WORK/run/bg.sh"

CALLS="$WORK/calls.log"

# STATEFUL stub. A stateless stub makes the script's own post-patch verification fail for a
# HARNESS reason (it re-reads the colour and sees the old value), which would mask what the
# scenario is actually testing. The patch is therefore recorded to a state file and the
# selector read answers from it.
cat > "$WORK/stubs/kubectl" <<'STUB'
#!/bin/bash
echo "kubectl $*" >> "$CALLS"
ARGS="$*"
STATE="${STUB_STATE_FILE:-/tmp/bg_state}"
case "$ARGS" in
  *'"value": "green"'*) echo green > "$STATE"; exit 0 ;;
  *'"value": "blue"'*)  echo blue  > "$STATE"; exit 0 ;;
esac
case "$ARGS" in
  *"jsonpath={.spec.selector.color}"*)
      if [ -s "$STATE" ]; then cat "$STATE"; else echo "${STUB_ACTIVE_COLOR:-blue}"; fi
      exit 0 ;;
  *"jsonpath={.spec.template.spec.containers[0].image}"*) echo "${STUB_IMAGE:-repo/app:oldtag}"; exit 0 ;;
  *"jsonpath={.spec.replicas}"*) echo "${STUB_REPLICAS:-3}"; exit 0 ;;
  *"jsonpath={.status.readyReplicas}"*) echo "${STUB_READY:-3}"; exit 0 ;;
  *"/health/ready"*) exit "${STUB_READY_EXIT:-0}" ;;
  *"/health"*)       exit "${STUB_HEALTH_EXIT:-0}" ;;
  *) exit 0 ;;
esac
STUB

cat > "$WORK/stubs/curl" <<'STUB'
#!/bin/bash
echo "curl $*" >> "$CALLS"
exit "${STUB_CURL_EXIT:-0}"
STUB

cat > "$WORK/stubs/sleep" <<'STUB'
#!/bin/bash
echo "sleep $*" >> "$CALLS"
exit 0
STUB

chmod +x "$WORK/stubs/"*

FAILURES=0
report() {
  if [ "$2" = "$3" ]; then printf '  PASS  %-52s exp=%-6s got=%-6s %s\n' "$1" "$2" "$3" "${4:-}"
  else printf '  FAIL  %-52s exp=%-6s got=%-6s %s\n' "$1" "$2" "$3" "${4:-}"; FAILURES=$((FAILURES+1)); fi
}

export STUB_STATE_FILE="$WORK/bg_state"

run_bg() {  # subcommand, extra-arg
  : > "$CALLS"; : > "$STUB_STATE_FILE"
  ( cd "$WORK/run" && CALLS="$CALLS" PATH="$WORK/stubs:$PATH" ./bg.sh "$1" "${2:-}" \
      >"$WORK/out.txt" 2>&1 )
  echo $?
}

echo "=== scripts/blue-green-deploy.sh — executed branch reproduction ==="
echo "script sha1: $(sha1sum "$SUT" | cut -d' ' -f1)   lines: $(wc -l < "$SUT")"
echo ""

# ── 1. validate_green: first health failure ───────────────────────────────────────────
echo "1 — deploy with the FIRST health probe failing (MAX_RETRIES=10)"
export STUB_ACTIVE_COLOR=blue STUB_HEALTH_EXIT=1 STUB_READY_EXIT=0
EXIT1="$(run_bg deploy repo/app:v2)"
HEALTH_PROBES=$(grep -c 'exec .*health' "$CALLS" || true)
report "exit code" "1" "$EXIT1"
report "health probes attempted before giving up" "10" "$HEALTH_PROBES" \
  "<- 1 means ZERO retries despite MAX_RETRIES=10"
grep -q 'Health check failed' "$WORK/out.txt" && echo "    (reported: Health check failed)"
echo ""

# ── 2. validate_green: success path ───────────────────────────────────────────────────
echo "2 — deploy with every health probe succeeding"
export STUB_HEALTH_EXIT=0
EXIT2="$(run_bg deploy repo/app:v2)"
OKS=$(grep -c 'Health check passed' "$WORK/out.txt" || true)
SLEEPS=$(grep -c '^sleep ' "$CALLS" || true)
report "exit code" "0" "$EXIT2"
report "consecutive successes required" "1" "$OKS" "<- 10 means it demands 10 in a row"
echo "    sleep calls on the success path: $SLEEPS"
echo ""

# ── 3. switch with NO prior validation ────────────────────────────────────────────────
echo "3 — 'switch' invoked standalone, green never validated"
: > "$CALLS"; : > "$STUB_STATE_FILE"
( cd "$WORK/run" && CALLS="$CALLS" PATH="$WORK/stubs:$PATH" \
    STUB_ACTIVE_COLOR=blue ./bg.sh switch >"$WORK/out.txt" 2>&1 )
EXIT3=$?
PATCHED=$(grep -c 'patch service' "$CALLS" || true)
VALIDATED=$(grep -c 'health' "$CALLS" || true)
report "traffic patch issued" "1" "$PATCHED"
report "health probe performed before the patch" "1" "$VALIDATED" \
  "<- 0 means traffic can cut to an unvalidated pod"
echo "    exit=$EXIT3 (note: switch verifies the SELECTOR it just wrote, not serving traffic)"
echo ""

# ── 4. promote: what happens to the rollback target ───────────────────────────────────
echo "4 — 'promote' while green is active"
: > "$CALLS"; echo green > "$STUB_STATE_FILE"
( cd "$WORK/run" && CALLS="$CALLS" PATH="$WORK/stubs:$PATH" \
    STUB_ACTIVE_COLOR=green ./bg.sh promote >"$WORK/out.txt" 2>&1 )
EXIT4=$?
GREEN_ZERO=$(grep -c 'scale deployment/trading-backend-green --replicas=0' "$CALLS" || true)
BLUE_IMG=$(grep -c 'set image deployment/trading-backend-blue' "$CALLS" || true)
echo "    exit=$EXIT4"
report "blue image overwritten with green's" "1" "$BLUE_IMG"
report "green scaled to 0 (rollback target destroyed)" "1" "$GREEN_ZERO"
echo "    => after promote, neither slot holds the PREVIOUS version; rollback has no target"
echo ""

# ── 5. rollback with blue having no ready replicas ────────────────────────────────────
echo "5 — 'rollback' while blue has 0 ready replicas"
: > "$CALLS"; echo green > "$STUB_STATE_FILE"
( cd "$WORK/run" && CALLS="$CALLS" PATH="$WORK/stubs:$PATH" \
    STUB_ACTIVE_COLOR=green STUB_READY=0 STUB_REPLICAS=0 ./bg.sh rollback \
    >"$WORK/out.txt" 2>&1 )
EXIT5=$?
ROLLBACK_OK=$(grep -c 'Rollback complete' "$WORK/out.txt" || true)
READY_CHECKED=$(grep -c 'readyReplicas' "$CALLS" || true)
report "rollback reported complete" "0" "$ROLLBACK_OK" \
  "<- 1 means success claimed with 0 ready pods"
echo "    exit=$EXIT5 · readyReplicas consulted $READY_CHECKED time(s) (status display only)"
echo ""

echo "=== harness expectation failures: $FAILURES ==="
echo "(a FAIL is the script's real behaviour differing from safe behaviour, not a harness fault)"
exit 0
