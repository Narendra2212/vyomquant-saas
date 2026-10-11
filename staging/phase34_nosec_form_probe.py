"""Phase 34 — does Bandit SCOPE the colon form `# nosec: B608`, or treat it as a bare nosec?

Read-only with respect to the repository: every fixture is written into a temp directory that
is deleted afterwards, and nothing under `backend_app/` is touched.

WHY THIS MATTERS. The corrected census (Phase 36, re-verified this phase) is 56 directives
across 17 files: **54 colon-form `# nosec: B608`**, 2 space-form, 0 bare. "Rule-scoped" has
been read as reassuring — a directive naming B608 suppresses only B608.

But Bandit's documented syntax is `# nosec` or `# nosec B101,B602` — SPACE separated. The
colon form is undocumented. There are two possibilities with very different consequences:

  (a) Bandit extracts `B608` despite the colon  -> genuinely scoped, census reading holds.
  (b) Bandit matches `# nosec` and fails to parse the ID list -> behaves as a BARE nosec and
      suppresses EVERY rule on that line, not just B608. 54 lines across 17 files would then
      be blanket-suppressed, which is materially broader than intended.

The experiment distinguishes them: annotate a finding of a DIFFERENT rule with
`# nosec: B608`. If that unrelated finding disappears, the directive is not scoping.

Usage:  .venv\\Scripts\\python.exe staging/phase34_nosec_form_probe.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

#: Each fixture: (filename, source, the rule we EXPECT to be reported if unsuppressed).
FIXTURES = {
    # Control 1 — B608 with no directive at all. Must be REPORTED.
    "c1_b608_unsuppressed.py": (
        "import sqlalchemy as sa\n"
        "def f(conn, table):\n"
        "    return conn.execute(sa.text('SELECT * FROM ' + table))\n",
        "B608",
    ),
    # Control 2 — B608 with the documented SPACE form. Must be SUPPRESSED.
    "c2_b608_space_form.py": (
        "import sqlalchemy as sa\n"
        "def f(conn, table):\n"
        "    return conn.execute(sa.text('SELECT * FROM ' + table))  # nosec B608\n",
        "B608",
    ),
    # Subject — B608 with the repository's COLON form. Suppressed or not?
    "s1_b608_colon_form.py": (
        "import sqlalchemy as sa\n"
        "def f(conn, table):\n"
        "    return conn.execute(sa.text('SELECT * FROM ' + table))  # nosec: B608\n",
        "B608",
    ),
    # Control 3 — a DIFFERENT rule (B602 subprocess shell=True), no directive. Must be REPORTED.
    "c3_other_rule_unsuppressed.py": (
        "import subprocess\n"
        "def g(cmd):\n"
        "    return subprocess.call(cmd, shell=True)\n",
        "B602",
    ),
    # THE DECISIVE CASE — a DIFFERENT rule annotated with `# nosec: B608`.
    #   suppressed  => the colon form acts as a BARE nosec (over-suppression)
    #   reported    => the colon form genuinely scopes to B608
    "s2_other_rule_colon_b608.py": (
        "import subprocess\n"
        "def g(cmd):\n"
        "    return subprocess.call(cmd, shell=True)  # nosec: B608\n",
        "B602",
    ),
    # Control 4 — the same different rule with the documented space form naming B608 only.
    #   Bandit should still REPORT B602, because the directive scopes to B608.
    "c4_other_rule_space_b608.py": (
        "import subprocess\n"
        "def g(cmd):\n"
        "    return subprocess.call(cmd, shell=True)  # nosec B608\n",
        "B602",
    ),
}


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="p34_nosec_"))
    try:
        for name, (src, _) in FIXTURES.items():
            (work / name).write_text(src, encoding="utf-8")

        # `-ll` matches the CI threshold in 05-security.yml:36. B602 is HIGH, B608 MEDIUM,
        # so both are at or above the threshold and the comparison is fair.
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", str(work), "-ll", "-f", "json"],
            capture_output=True, text=True,
        )
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print("BLOCKED: bandit produced no JSON report")
            print("exit=%s stderr=%s" % (proc.returncode, proc.stderr[:800]))
            return 1

        found = {}
        for item in report.get("results", []):
            found.setdefault(pathlib.Path(item["filename"]).name, set()).add(item["test_id"])

        print("bandit exit=%d   command: -m bandit -r <tmp> -ll -f json" % proc.returncode)
        print("")
        print("  %-34s %-9s %-11s %s" % ("fixture", "expect", "reported", "verdict"))
        print("  " + "-" * 78)
        verdicts = {}
        for name, (_, rule) in FIXTURES.items():
            reported = rule in found.get(name, set())
            verdicts[name] = reported
            print("  %-34s %-9s %-11s %s" % (
                name, rule, "YES" if reported else "no",
                "" if name.startswith("c") else "<-- SUBJECT",
            ))

        print("")
        ok = (
            verdicts["c1_b608_unsuppressed.py"] is True
            and verdicts["c2_b608_space_form.py"] is False
            and verdicts["c3_other_rule_unsuppressed.py"] is True
        )
        if not ok:
            print("  CONTROLS DID NOT BEHAVE AS EXPECTED — the experiment is INVALID.")
            print("  c1 (must report)=%s  c2 (must suppress)=%s  c3 (must report)=%s" % (
                verdicts["c1_b608_unsuppressed.py"],
                verdicts["c2_b608_space_form.py"],
                verdicts["c3_other_rule_unsuppressed.py"],
            ))
            return 1
        print("  controls OK: unannotated findings are reported, the documented space form "
              "suppresses.")
        print("")

        colon_suppresses_b608 = not verdicts["s1_b608_colon_form.py"]
        colon_suppresses_other = not verdicts["s2_other_rule_colon_b608.py"]
        space_scopes = verdicts["c4_other_rule_space_b608.py"]

        print("  FINDINGS")
        print("    colon form suppresses its named rule (B608) : %s" % colon_suppresses_b608)
        print("    colon form ALSO suppresses an unrelated rule: %s" % colon_suppresses_other)
        print("    space form leaves the unrelated rule reported: %s" % space_scopes)
        print("")
        if colon_suppresses_other:
            print("  >>> THE COLON FORM DOES NOT SCOPE. `# nosec: B608` behaves as a BARE")
            print("      `# nosec` and suppresses EVERY rule on its line.")
            print("      54 such directives across 17 files are therefore blanket")
            print("      suppressions, not rule-scoped ones.")
        else:
            print("  >>> The colon form scopes correctly. The census reading holds and the")
            print("      54 directives suppress B608 only.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
