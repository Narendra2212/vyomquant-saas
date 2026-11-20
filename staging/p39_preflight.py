#!/usr/bin/env python3
"""PHASE 39 / PROTOTYPE ONLY -- environment preflight and shell calibration.

Section 2.2 requires the shell to be CALIBRATED before any deployment test is
trusted, and section 2 forbids repeatedly invoking a broken launcher. This makes
exactly ONE attempt per calibration step and stops at the first hard failure.

Reads only. Writes nothing except stdout.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys


def head(text: str) -> None:
    print("")
    print("=" * 92)
    print(text)
    print("=" * 92)


head("A. Interpreters and libraries")
print("")
print("  python   : %s" % sys.version.split()[0])
for mod in ("pytest", "bandit", "yaml", "hypothesis"):
    spec = importlib.util.find_spec(mod)
    ver = ""
    if spec:
        try:
            ver = __import__(mod).__version__
        except Exception:
            ver = "(version unknown)"
    print("  %-9s: %s %s" % (mod, "PRESENT" if spec else "ABSENT", ver))

head("B. External tools")
print("")
for tool in ("bash", "gh", "git", "docker", "trivy", "terraform",
             "kubectl", "safety", "pip-audit", "wsl"):
    path = shutil.which(tool)
    print("  %-11s %-8s %s" % (tool, "PRESENT" if path else "ABSENT", path or ""))

head("C. Python subprocess capability")
print("")
try:
    p = subprocess.run([sys.executable, "-c", "print('subproc-ok')"],
                       capture_output=True, timeout=30)
    print("  exit=%d stdout=%r" % (p.returncode, p.stdout.decode().strip()))
    print("  => python subprocess execution WORKS")
except Exception as exc:
    print("  => python subprocess execution FAILS: %s" % exc)

head("D. Shell calibration (one attempt per step, stop at first hard failure)")
print("")
SHELL_OK = False
bash = shutil.which("bash")
if not bash:
    print("  step 1  bash not on PATH -> SHELL NOT CALIBRATED")
else:
    print("  bash resolved to: %s" % bash)
    try:
        p = subprocess.run([bash, "-c", "echo CAL_MARKER_1; exit 7"],
                           capture_output=True, timeout=45)
        out = p.stdout.decode("utf-8", "replace").strip()
        err = p.stderr.decode("utf-8", "replace").strip()
        print("  step 1  trivial command")
        print("          exit=%s" % p.returncode)
        print("          stdout=%r" % out[:160])
        if err:
            print("          stderr=%r" % err[:300])
        if "CAL_MARKER_1" in out and p.returncode == 7:
            print("          PASS: marker echoed AND exit status propagated")
            SHELL_OK = True
        elif "CAL_MARKER_1" in out:
            print("          FAIL: marker seen but exit status not propagated")
        else:
            print("          FAIL: interpreter did not run the command")
    except subprocess.TimeoutExpired:
        print("  step 1  TIMEOUT -> launcher hung; not retrying")
    except Exception as exc:
        print("  step 1  launch error: %s" % exc)

print("")
print("  SHELL CALIBRATED: %s" % SHELL_OK)
if not SHELL_OK:
    print("  => every shell-dependent test is BLOCKED -- TOOL/SERVICE ABSENT.")
    print("     Phase 38's deploy-harness execution result stays HISTORICAL and is")
    print("     NOT revalidated this phase. It is not re-asserted as current.")

head("E. Services")
print("")
for label, port in (("Redis", 6379), ("PostgreSQL 51815", 51815),
                    ("PostgreSQL 51805", 51805), ("PostgreSQL 5432", 5432)):
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        state = "OPEN"
    except OSError:
        state = "CLOSED"
    finally:
        s.close()
    print("  %-18s %s" % (label, state))
print("")
for var in ("REDIS_URL", "DATABASE_URL", "APP_DATABASE_URL"):
    print("  %-18s set=%s" % (var, bool(os.environ.get(var))))
print("  (values deliberately not printed)")

head("F. Disk")
print("")
total, used, free = shutil.disk_usage("C:\\")
print("  C: free      : %.1f MB" % (free / 1048576))
print("  C: total     : %.1f GB" % (total / 1073741824))
print("")
if free < 100 * 1048576:
    print("  CONSTRAINED. Artifact writes may report success and produce 0 bytes.")
    print("  Every artifact this phase must be size-verified after writing.")
