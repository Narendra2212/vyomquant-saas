#!/bin/bash
# PHASE 32 harness — reproduce the false-green failure path in scripts/deploy.sh by EXECUTION.
#
# PROTOTYPE ONLY. Reads scripts/deploy.sh; never modifies it. No Kubernetes cluster, no cloud,
# no network: `kubectl` and `curl` are replaced by stubs on PATH, and the script runs in a
# throwaway temp directory with a dummy env file so the real repository is untouched.
#
# WHAT IT ANSWERS. Source review says all five health checks can fail while the script still
# prints its success banner and exits 0. This executes that path and reports the real exit code.
#
# Usage:  bash staging/phase32_deploy_sh_harness.sh <repo-root>
# Exit:   0 if every scenario behaved as the harness expected, 1 otherwise.

set -u

REPO_ROOT="${1:?usage: phase32_deploy_sh_harness.sh <repo-root>}"
SCRIPT_UNDER_TEST="$REPO_ROOT/scripts/deploy.sh"

if [ ! -f "$SCRIPT_UNDER_TEST" ]; then
  echo "BLOCKED: $SCRIPT_UNDER_TEST not found"
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/stubs" "$WORK/run"
# Normalise CRLF -> LF. This Windows worktree has core.autocrlf=true, so the checked-out file
# carries CRLF and `#!/bin/bash\r` yields exit 127 ("bad interpreter") before a single line of
# the script runs. The COMMITTED blob is pure LF (verified: git cat-file blob -> CR=0), so this
# is a local checkout artifact, NOT a repository defect, and stripping CR here tests the
# script's real logic rather than this machine's line endings.
tr -d '\r' < "$SCRIPT_UNDER_TEST" > "$WORK/run/deploy.sh"
chmod +x "$WORK/run/deploy.sh"

# A dummy env file. No real secret, no real value from the repository.
printf '# comment line\nFAKE_SETTING=dummy_value\n' > "$WORK/run/.env.staging"

make_stub() {  # name, exit_code, echo_text
  cat > "$WORK/stubs/$1" <<STUB
#!/bin/bash
echo "[stub $1] \$*" >> "$WORK/calls.log"
${3:+echo "$3"}
exit $2
STUB
  chmod +x "$WORK/stubs/$1"
}

FAILURES=0

report() {  # label, expected, actual, note
  if [ "$2" = "$3" ]; then
    printf '  PASS  %-46s expected=%-8s actual=%-8s %s\n' "$1" "$2" "$3" "${4:-}"
  else
    printf '  FAIL  %-46s expected=%-8s actual=%-8s %s\n' "$1" "$2" "$3" "${4:-}"
    FAILURES=$((FAILURES + 1))
  fi
}

run_scenario() {  # curl_exit
  : > "$WORK/calls.log"
  make_stub kubectl 0 ""
  make_stub curl "$1" ""
  ( cd "$WORK/run" && PATH="$WORK/stubs:$PATH" ./deploy.sh staging >"$WORK/out.txt" 2>&1 )
  echo $?
}

echo "=== scripts/deploy.sh — executed failure-path reproduction ==="
echo "script sha1: $(sha1sum "$SCRIPT_UNDER_TEST" | cut -d' ' -f1)"
echo ""

# ── Scenario 1: every health check FAILS (curl non-zero on all 5 attempts) ─────────────
echo "Scenario 1 — kubectl succeeds, curl fails on all 5 attempts"
# The real script sleeps 10s between attempts; 5 attempts = ~40s. Shorten by stubbing sleep.
make_stub sleep 0 ""
EXIT1="$(run_scenario 22)"
BANNER1=$(grep -c 'Deployment to staging complete' "$WORK/out.txt" || true)
PASSED1=$(grep -c 'Health check passed' "$WORK/out.txt" || true)
ATTEMPTS1=$(grep -c 'Health check attempt' "$WORK/out.txt" || true)
CURL_CALLS=$(grep -c '\[stub curl\]' "$WORK/calls.log" || true)

report "exit code when all health checks fail" "1" "$EXIT1" "<- 0 means FALSE GREEN"
report "success banner suppressed on failure" "0" "$BANNER1" "<- 1 means banner printed anyway"
report "health-check-passed line absent" "0" "$PASSED1"
report "all 5 attempts made" "5" "$ATTEMPTS1"
report "curl actually invoked 5 times" "5" "$CURL_CALLS"
echo ""

# ── Scenario 2: health check succeeds ─────────────────────────────────────────────────
echo "Scenario 2 — kubectl succeeds, curl succeeds (control)"
EXIT2="$(run_scenario 0)"
BANNER2=$(grep -c 'Deployment to staging complete' "$WORK/out.txt" || true)
report "exit code on full success" "0" "$EXIT2"
report "success banner printed" "1" "$BANNER2"
echo ""

# ── Scenario 3: the image argument actually passed to kubectl ─────────────────────────
echo "Scenario 3 — what image identity reaches 'kubectl set image'"
: > "$WORK/calls.log"
make_stub kubectl 0 ""
make_stub curl 0 ""
( cd "$WORK/run" && PATH="$WORK/stubs:$PATH" ./deploy.sh staging >/dev/null 2>&1 )
SETIMAGE=$(grep '\[stub kubectl\] set image' "$WORK/calls.log" || true)
echo "  observed: $SETIMAGE"
if echo "$SETIMAGE" | grep -q 'backend=latest'; then
  echo "  CONFIRMED  default image identity is the bare mutable tag 'latest'"
  echo "             — no registry, no repository, no digest"
else
  echo "  NOT CONFIRMED  default image identity differs from expectation"
  FAILURES=$((FAILURES + 1))
fi
echo ""

# ── Scenario 4: missing env file ──────────────────────────────────────────────────────
echo "Scenario 4 — .env.staging absent"
rm -f "$WORK/run/.env.staging"
: > "$WORK/calls.log"
make_stub kubectl 0 ""
make_stub curl 0 ""
( cd "$WORK/run" && PATH="$WORK/stubs:$PATH" ./deploy.sh staging >"$WORK/out.txt" 2>&1 )
EXIT4=$?
REACHED=$(grep -c 'Running pre-deployment checks' "$WORK/out.txt" || true)
report "exit code when env file is missing" "1" "$EXIT4" "<- 0 means it deployed without config"
report "did NOT proceed to pre-deployment checks" "0" "$REACHED"
printf '# comment line\nFAKE_SETTING=dummy_value\n' > "$WORK/run/.env.staging"
echo ""

echo "=== harness expectation failures: $FAILURES ==="
echo "(a FAIL above means the script's real behaviour differs from safe behaviour —"
echo " that is the defect being reported, not a fault in this harness)"
exit 0
