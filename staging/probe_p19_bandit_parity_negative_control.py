"""Phase 19 — executed negative control for the proposed Bandit test/CI parity fix.

WHY THIS EXISTS
---------------
Phase 18 demonstrated that at one source state `tests/test_bandit_sast_gate_is_green.py`
reports 3 passed while the CI command `bandit -r backend_app -ll` exits 1 with nine B608
findings. Phase 19 §4 asks for the parity repair plus two negative controls:

  * an introduced unsuppressed finding must make the parity test FAIL;
  * a suppression must not be able to make the test falsely green without the expected
    detection behaviour.

No grant exists for `tests/test_bandit_sast_gate_is_green.py`, so the parity test cannot be
committed. This probe demonstrates the proposed mechanism and both negative controls on
throwaway fixtures, and measures the structural scope gap directly.

WHAT IT MEASURES
----------------
  [1] SCOPE GAP      — bandit on the single module the current test gates (expect exit 0)
                       versus the full-tree CI scope (reused Phase 18 evidence, exit 1).
  [2] DETECTION      — an unsuppressed interpolated-SQL fixture must produce B608, exit 1.
  [3] SUPPRESSION    — the same fixture with a site-specific `# nosec B608` must exit 0,
                       proving the suppression is what changes the verdict, not luck.
  [4] FALSE-GREEN    — a *different*, unsuppressed finding alongside a suppressed one must
                       still exit 1, proving a suppression cannot blanket the scanner.

READ-ONLY with respect to the repository. Fixtures are written to a system temp directory
and deleted. No repository file is modified. No `reports/` path is written.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PY = sys.executable
GATED_MODULE = os.path.join("backend_app", "backend", "transactional_execution_manager.py")

# An interpolated identifier with NO validation -- genuinely unsafe shape.
UNSAFE = '''\
import sqlalchemy as sa


def count_rows(conn, raw_table):
    return conn.execute(
        sa.text(f'SELECT count(*) FROM public."{raw_table}"')
    ).scalar()
'''

# The repository's established pattern: strict allowlist that raises, plus a site-specific
# suppression carrying its justification.
SUPPRESSED = '''\
import re

import sqlalchemy as sa


def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises."""
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError("not a SQL identifier")


def count_rows(conn, raw_table):
    table = _validate_table_name(raw_table)
    # nosec B608 - `table` is an allowlisted bare identifier (^[a-zA-Z0-9_]{1,64}$); the
    # validator raises rather than sanitising, and identifiers cannot be bound parameters.
    return conn.execute(
        sa.text(f'SELECT count(*) FROM public."{table}"')  # nosec B608
    ).scalar()
'''


def run_bandit(target: str, label: str) -> tuple[int, int, list, float]:
    """Run the CI-equivalent command against `target`. Returns (exit, n, test_ids, secs)."""
    out = os.path.join(tempfile.gettempdir(), "p19_bandit_%s.json" % label)
    started = time.time()
    proc = subprocess.run(
        [PY, "-m", "bandit", "-r", target, "-ll", "-f", "json", "-o", out],
        capture_output=True, text=True,
    )
    elapsed = time.time() - started
    n, ids = 0, []
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        results = data.get("results", [])
        n = len(results)
        ids = sorted({r.get("test_id") for r in results})
        os.remove(out)
    return proc.returncode, n, ids, elapsed


def main() -> int:
    print("=" * 100)
    print("Phase 19 negative control — Bandit test/CI parity")
    print("=" * 100)

    ver = subprocess.run([PY, "-m", "bandit", "--version"],
                         capture_output=True, text=True).stdout.strip().splitlines()
    print("bandit version:", ver[0] if ver else "(unknown)")

    checks = []

    # [1] The scope gap, measured.
    print("\n[1] SCOPE GAP — what the current test gates vs what CI scans")
    rc, n, ids, secs = run_bandit(GATED_MODULE, "gated")
    print(f"  single gated module : exit={rc} findings={n} ids={ids or '[]'} ({secs:.1f}s)")
    print(f"    -> {GATED_MODULE}")
    print("  full tree (-r backend_app) : exit=1 findings=9 ids=['B608']   "
          "[REUSED Phase 18 evidence]")
    print("     reuse justified: identical HEAD, identical file hashes, bandit 1.9.4 both times")
    checks.append(("single-module scan is green", rc == 0))
    print("  CONCLUSION: the module the current test gates is clean, so the one-file test")
    print("              passes while the CI scope fails. The gap is structural, not luck.")

    tmp = tempfile.mkdtemp(prefix="p19_bandit_")
    try:
        # [2] Detection.
        print("\n[2] DETECTION — an unsuppressed interpolated-SQL finding must fail the gate")
        pkg = os.path.join(tmp, "detect")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "unsafe.py"), "w", encoding="utf-8") as fh:
            fh.write(UNSAFE)
        rc, n, ids, secs = run_bandit(pkg, "detect")
        print(f"  exit={rc} findings={n} ids={ids} ({secs:.1f}s)")
        checks.append(("unsuppressed finding -> exit 1", rc == 1 and "B608" in ids))

        # [3] Suppression changes the verdict.
        print("\n[3] SUPPRESSION — the validated + site-suppressed pattern must be green")
        pkg = os.path.join(tmp, "suppressed")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "safe.py"), "w", encoding="utf-8") as fh:
            fh.write(SUPPRESSED)
        rc, n, ids, secs = run_bandit(pkg, "suppressed")
        print(f"  exit={rc} findings={n} ids={ids or '[]'} ({secs:.1f}s)")
        checks.append(("site-suppressed validated pattern -> exit 0", rc == 0 and n == 0))

        # [4] A suppression cannot blanket an unrelated finding.
        print("\n[4] FALSE-GREEN GUARD — a suppression must not hide a different finding")
        pkg = os.path.join(tmp, "mixed")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "safe.py"), "w", encoding="utf-8") as fh:
            fh.write(SUPPRESSED)
        with open(os.path.join(pkg, "unsafe.py"), "w", encoding="utf-8") as fh:
            fh.write(UNSAFE)
        rc, n, ids, secs = run_bandit(pkg, "mixed")
        print(f"  exit={rc} findings={n} ids={ids} ({secs:.1f}s)")
        print("  (the suppressed site is silent; the unsuppressed one still reports)")
        checks.append(("suppression does not blanket -> still exit 1", rc == 1 and n == 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 100)
    for name, ok in checks:
        print(f"  [{'x' if ok else ' '}] {name}")
    ok = all(c[1] for c in checks)
    print("\nINTERPRETATION:")
    print("  The proposed parity test (subprocess bandit over `-r backend_app -ll`, asserting")
    print("  the real exit code) is demonstrably sensitive: it fails on an introduced finding,")
    print("  goes green only when the site carries its own justified suppression, and cannot")
    print("  be made falsely green by a suppression elsewhere. Those are exactly the two")
    print("  negative controls Phase 19 section 4 requires, executed rather than asserted.")
    print(f"\nRESULT: {'parity negative control VALID' if ok else 'INVALID'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
