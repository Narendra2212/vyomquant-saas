#!/usr/bin/env python3
"""PHASE 39 / PROTOTYPE ONLY -- what does deploy.sh:21 do to a quoted value?

Phase 38 claimed `export $(grep -v '^#' .env.$ENV | xargs)` FAILS the deployment
on ordinary quoted values containing spaces. The Phase 39 harness re-run
contradicts that: the refined scenario exits 0 and prints the success banner.
This isolates the idiom to settle which is right.

Runs in a throwaway WSL /tmp directory. Touches no repository file.

TWO DISCARDED REVISIONS, recorded so the result is not over-trusted:
  1. `bash -s` fed a script containing a heredoc -> hung, no output. Discarded.
  2. `bash -c "<long script>"` -> the Windows/WSL interop layer re-parsed the
     command line and the quoting did not survive; the second `grep` ran without
     its file and `export` was called with no operands, which merely prints the
     environment. Discarded.
This revision feeds the script on stdin and builds the fixture with printf.
"""
from __future__ import annotations

import shutil
import subprocess

SCRIPT = r"""
set -u
W=$(mktemp -d /tmp/p39env_XXXXXX)
cd "$W" || exit 90

printf '%s\n' '# a comment' > .env.staging
printf '%s\n' 'API_URL=https://staging.example.com' >> .env.staging
printf '%s\n' '' >> .env.staging
printf '%s\n' 'QUOTED="a value with spaces"' >> .env.staging

echo "=== fixture .env.staging ==="
cat .env.staging
echo "file exists: $([ -f .env.staging ] && echo yes || echo no)"

echo ""
echo "=== substitution result (quotes already stripped by xargs) ==="
grep -v '^#' .env.staging | xargs

echo ""
echo "=== run the exact idiom from deploy.sh:21, under set -e ==="
set -e
export $(grep -v '^#' .env.staging | xargs)
RC=$?
set +e
echo "idiom exit status: $RC"

echo ""
echo "=== resulting values ==="
printf 'API_URL = [%s]\n' "${API_URL-<unset>}"
printf 'QUOTED  = [%s]\n' "${QUOTED-<unset>}"

echo ""
echo "=== stray words now marked for export ==="
for n in value with spaces; do
  if export -p | grep -q "^declare -x ${n}\$"; then
    echo "  stray name exported: ${n}"
  fi
done
echo "(end of stray list)"

cd /
rm -rf "$W"
"""


def main() -> int:
    bash = shutil.which("bash")
    if not bash:
        print("bash ABSENT -> BLOCKED -- TOOL/SERVICE ABSENT")
        return 2
    try:
        proc = subprocess.run([bash, "-s"], input=SCRIPT.encode("utf-8"),
                              capture_output=True, timeout=150)
    except subprocess.TimeoutExpired:
        print("bash TIMED OUT -> discarded, not reported as a finding")
        return 3

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    print(out)
    noise = [ln for ln in err.splitlines()
             if ln.strip() and "systemd user session" not in ln]
    if noise:
        print("--- stderr ---")
        for ln in noise:
            print("  " + ln)
    print("script exit status: %d" % proc.returncode)

    print("")
    print("=" * 88)
    print("READING")
    print("=" * 88)
    ok_file = "file exists: yes" in out
    truncated = "QUOTED  = [a]" in out
    intact = "QUOTED  = [a value with spaces]" in out

    if not ok_file:
        print("  Fixture was not created -> probe invalid, no claim made.")
        return 0
    if truncated and proc.returncode == 0:
        print("  CONFIRMED: the quoted value is SILENTLY TRUNCATED to its first word")
        print("  and the script continues with exit status 0.")
        print("")
        print("  Mechanism: `xargs` strips the quotes, then `export` word-splits the")
        print('  substitution. `QUOTED="a value with spaces"` becomes')
        print("  `export QUOTED=a value with spaces`, which bash reads as: assign")
        print("  QUOTED=a, then mark the names `value`, `with`, `spaces` for export.")
        print("  Those are valid identifiers, so `export` returns 0 and `set -e` sees")
        print("  success.")
        print("")
        print("  This CORRECTS Phase 38's D38-3. The defect is NOT that well-formed")
        print("  configuration fails the deployment. It is that configuration is")
        print("  silently corrupted while the deployment reports success. That is")
        print("  worse than failing, because a hard failure would be visible.")
        print("")
        print("  The idiom fails hard only when a split word is not a valid")
        print("  identifier -- e.g. one containing parentheses (scenario N).")
    elif intact:
        print("  The value survived intact. Phase 38's reading and this probe's")
        print("  hypothesis are BOTH wrong; no truncation occurs.")
    elif proc.returncode != 0:
        print("  The idiom FAILED on this input. Phase 38's reading would stand.")
    else:
        print("  Inconclusive -- inspect the values above. No claim made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
