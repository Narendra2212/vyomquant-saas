#!/usr/bin/env python3
"""PHASE 38 / PROTOTYPE ONLY -- why does the local bandit invocation exit 1?

An exit code of 1 with zero findings and no metrics is not a scan verdict, it is
an error. This separates the two before any claim is made about CI.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

proc = subprocess.run(
    [sys.executable, "-m", "bandit", "-r", "backend_app", "-ll", "-f", "json"],
    cwd=str(REPO), capture_output=True,
)
out = proc.stdout.decode("utf-8", "replace")
err = proc.stderr.decode("utf-8", "replace")

print("returncode      : %d" % proc.returncode)
print("stdout bytes    : %d" % len(proc.stdout))
print("stderr bytes    : %d" % len(proc.stderr))
print("")
print("--- stdout, first 400 chars ---")
print(out[:400])
print("")
print("--- stderr, last 2500 chars ---")
print(err[-2500:])
print("")

try:
    doc = json.loads(out)
except json.JSONDecodeError as exc:
    print("stdout is NOT valid JSON: %s" % exc)
    doc = None

if doc is not None:
    results = doc.get("results", [])
    errors = doc.get("errors", [])
    totals = doc.get("metrics", {}).get("_totals", {})
    print("parsed OK")
    print("  results : %d" % len(results))
    print("  errors  : %d" % len(errors))
    for e in errors[:10]:
        print("     %s" % e)
    print("  loc     : %s" % totals.get("loc"))
    print("  nosec   : %s" % totals.get("nosec"))
    sev = {k: totals.get(k) for k in totals if k.startswith("SEVERITY")}
    print("  severity totals: %s" % sev)
