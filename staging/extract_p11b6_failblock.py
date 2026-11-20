"""PHASE 11B-A6 — which failure tracebacks did GitHub actually return? Read-only."""
import pathlib
import re

LOG = pathlib.Path(__file__).resolve().parent / "p11b6_logfailed.txt"
_head = LOG.read_bytes()[:4]
_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
lines = [re.sub(r"^.*?\t\S+Z ", "", ln).rstrip()
         for ln in LOG.read_text(encoding=_enc, errors="replace").splitlines()]

start = next((i for i, ln in enumerate(lines) if "= FAILURES =" in ln), None)
print("=" * 78)
print(f"log lines: {len(lines)}   FAILURES block starts at: {start}")
print("=" * 78)

if start is None:
    raise SystemExit("no FAILURES block")

headers = []
for ln in lines[start + 1:]:
    s = ln.strip()
    if s.startswith("_") and s.endswith("_") and len(s) > 10:
        name = s.strip("_ ").strip()
        if name:
            headers.append(name)

print(f"traceback headers present in the returned log: {len(headers)}")
for h in headers[:60]:
    print("   " + h[:120])

D5 = ("slot_lock_is_mutually", "guard_admits_exactly", "lock_is_recorded_in_redis",
      "in_flight_delivery_blocks")
print("-" * 78)
print("are the four D5' failures among them?")
for token in D5:
    hit = any(token in h for h in headers)
    print(f"   {token:<30} {'YES' if hit else 'NO'}")

print("-" * 78)
print(f"lines after the FAILURES header: {len(lines) - start}")
print("last 6 lines of the file:")
for ln in [l for l in lines if l.strip()][-6:]:
    print("   " + ln[:150])
