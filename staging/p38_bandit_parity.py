#!/usr/bin/env python3
"""
PHASE 38 / PROTOTYPE ONLY -- Bandit CI-parity and suppression-syntax measurement.

Three questions:
  A. Does the exact CI command fail on current source, and why?
  B. Does the repository's gate test cover the same scope CI scans?
  C. Are the `# nosec` directives syntactically what Bandit expects?

MEASUREMENT NOTE. `bandit -f json` writes a progress bar to STDOUT ahead of the
JSON document, so naively json.loads()-ing stdout fails and looks like "0 findings,
exit 1" -- i.e. like an error rather than a verdict. An earlier revision of this
script reported exactly that. The prefix is stripped here. In CI this never arises
because `-o reports/bandit-report.json` sends the document to a file.

Nothing is written to disk: the host volume is full.
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GATE_TEST = REPO / "tests" / "test_bandit_sast_gate_is_green.py"


def run_bandit(args: list[str]) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "bandit"] + args + ["-f", "json"],
        cwd=str(REPO), capture_output=True,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    brace = out.find("{")
    doc: dict = {}
    if brace != -1:
        try:
            doc = json.loads(out[brace:])
        except json.JSONDecodeError:
            doc = {}
    return proc.returncode, doc, err


def section(title: str) -> None:
    print("")
    print("=" * 96)
    print(title)
    print("=" * 96)


def main() -> int:
    section("A. The command CI actually runs")
    print("  05-security.yml:36")
    print("    bandit -r backend_app -ll -f json -o reports/bandit-report.json")
    print("  No continue-on-error, so a nonzero exit fails the step AND the job.")
    print("")

    rc, doc, err = run_bandit(["-r", "backend_app", "-ll"])
    results = doc.get("results", [])
    totals = doc.get("metrics", {}).get("_totals", {})
    sev = {k.split(".")[1]: v for k, v in totals.items() if k.startswith("SEVERITY")}

    print("  measured exit code : %d" % rc)
    print("  findings reported  : %d" % len(results))
    print("  severity totals    : %s" % sev)
    print("  scan errors        : %d" % len(doc.get("errors", [])))
    print("  loc scanned        : %s" % totals.get("loc"))
    print("  nosec-suppressed   : %s" % totals.get("nosec"))
    print("")
    if rc != 0 and results:
        print("  => `Run Bandit Scan` FAILS on current source, on real findings")
        print("     (not on a scanner error: errors=0 and metrics are populated).")
    elif rc != 0:
        print("  => nonzero exit with no findings: treat as a scanner ERROR, not a verdict.")
    else:
        print("  => `Run Bandit Scan` passes on current source.")

    if results:
        print("")
        print("  by test id  : %s" % dict(collections.Counter(x["test_id"] for x in results)))
        print("")
        per_file: dict[str, list[int]] = collections.defaultdict(list)
        for item in results:
            per_file[item["filename"]].append(item["line_number"])
        print("  %-66s %-4s %s" % ("file", "n", "lines"))
        print("  %-66s %-4s %s" % ("-" * 66, "-" * 4, "-" * 18))
        for path, lines in sorted(per_file.items()):
            print("  %-66s %-4d %s" % (path, len(lines),
                                       ",".join(str(n) for n in sorted(lines))))

    # ------------------------------------------------------------------ B
    section("B. Scope covered by the repository's gate test")
    if not GATE_TEST.exists():
        print("  %s  ABSENT" % GATE_TEST.relative_to(REPO).as_posix())
    else:
        src = GATE_TEST.read_text(encoding="utf-8")
        tree = ast.parse(src)
        print("  file  : %s" % GATE_TEST.relative_to(REPO).as_posix())
        print("  lines : %d" % len(src.splitlines()))

        pinned = sorted(set(re.findall(r"[\w/\\.]+\.py", src)))
        pinned = [p for p in pinned if "backend_app" in p]
        print("")
        print("  backend_app paths named in the test (%d):" % len(pinned))
        for path in pinned:
            print("    %s" % path)

        tests = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test")]
        print("")
        print("  test functions (%d):" % len(tests))
        for name in tests:
            print("    %s" % name)

        invokes = bool(re.search(r"subprocess|bandit\.core|run_bandit", src))
        print("")
        print("  does it invoke bandit itself?        %s" % invokes)
        print("  does it scan `-r backend_app`?       %s"
              % bool(re.search(r"-r['\"]?\s*,?\s*['\"]?backend_app['\"]?", src)))

        if pinned and results:
            gate_files = {pathlib.Path(p).name for p in pinned}
            outside = sum(1 for x in results
                          if pathlib.Path(x["filename"]).name not in gate_files)
            inside = len(results) - outside
            print("")
            print("  CI findings inside the paths the test names : %d" % inside)
            print("  CI findings OUTSIDE those paths             : %d" % outside)
            if outside:
                print("")
                print("  SCOPE GAP CONFIRMED: %d of %d current findings lie outside the"
                      % (outside, len(results)))
                print("  files this gate test names. A green gate is not evidence of a")
                print("  green pipeline.")

    # ------------------------------------------------------------------ C
    section("C. Suppression-comment syntax, as Bandit itself parses it")
    in_comment = re.findall(r"Test in comment: (\S+) is not a test name or id", err)
    stale = re.findall(r"nosec encountered \((\S+)\), but no failed test on file (\S+)", err)

    print("  Bandit parses everything after `# nosec` as a comma/space separated list")
    print("  of test ids. Prose rationale is therefore parsed as test ids and rejected.")
    print("")
    print("  distinct rejected tokens : %d" % len(set(in_comment)))
    print("  total rejection warnings : %d" % len(in_comment))
    if in_comment:
        common = collections.Counter(in_comment).most_common(12)
        print("")
        print("  most frequent rejected tokens:")
        for token, count in common:
            print("    %-28s %d" % (token, count))

    # Would any rationale word collide with a REAL bandit test id or name?
    try:
        from bandit.core import extension_loader  # noqa: WPS433
        mgr = extension_loader.MANAGER
        real = set(mgr.plugins_by_id) | set(mgr.plugins_by_name)
        collide = sorted({t for t in in_comment if t in real})
        print("")
        print("  known Bandit test ids+names: %d" % len(real))
        print("  rejected tokens that ARE real bandit tests: %d" % len(collide))
        if collide:
            print("    %s" % ", ".join(collide))
            print("    ^ these would SILENTLY WIDEN the suppression, not be ignored.")
        else:
            print("    none -- so today every stray word is ignored with a warning.")
            print("    The risk is latent: a rationale containing a real test name or a")
            print("    B-number would extend the suppression without any diagnostic.")
    except Exception as exc:  # pragma: no cover
        print("  (could not load bandit plugin registry: %s)" % exc)

    print("")
    print("  suppressions that suppressed nothing (stale): %d" % len(stale))
    for test_id, path in sorted(set(stale)):
        print("    %-8s %s" % (test_id, path))
    if stale:
        print("    ^ a `# nosec` for a test that does not fire there. Either the code")
        print("      changed and the directive was left behind, or it is on the wrong line.")

    print("")
    print("  No suppression was added, removed, moved or edited by this measurement.")
    print("  No severity threshold was changed. No finding was reclassified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
