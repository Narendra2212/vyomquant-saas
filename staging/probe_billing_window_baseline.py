"""Measure the same anchor->needle distance in the COMMITTED billing.py.

Decides attribution: if the distance already exceeded 3000 at HEAD, the test was already
failing and the remediation did not cause it. If it was under 3000 at HEAD and is over
now, the remediation pushed the string out of the window and owns the failure.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REL = "backend_app/routers/billing.py"

committed = subprocess.run(
    ["git", "show", f"HEAD:{REL}"],
    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
)
if committed.returncode != 0:
    raise SystemExit(f"git show failed: {committed.stderr[:400]}")

anchor = "invoice.payment_succeeded"
needle = "Subscription Renewed"

for label, source in (
    ("HEAD (committed)", committed.stdout),
    ("working tree", (ROOT / REL).read_text(encoding="utf-8")),
):
    idx = source.find(anchor)
    pos = source.find(needle)
    distance = (pos - idx) if (idx != -1 and pos != -1) else None
    print(f"{label}:")
    print(f"  anchor offset  = {idx}")
    print(f"  needle offset  = {pos}")
    print(f"  distance       = {distance}")
    if distance is None:
        print("  window verdict = N/A (one of the strings is absent)")
    else:
        print(f"  window verdict = {'INSIDE (test passes)' if distance < 3000 else 'OUTSIDE (test fails)'}")
    print(f"  file length    = {len(source)}")
    print()

ic = committed.stdout.find(anchor)
pc = committed.stdout.find(needle)
wt = (ROOT / REL).read_text(encoding="utf-8")
iw = wt.find(anchor)
pw = wt.find(needle)
if None not in (ic, pc, iw, pw) and -1 not in (ic, pc, iw, pw):
    print(f"ATTRIBUTION: distance moved {(pw - iw) - (pc - ic):+d} chars "
          f"({pc - ic} -> {pw - iw}).")
