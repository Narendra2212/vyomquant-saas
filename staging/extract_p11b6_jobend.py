"""PHASE 11B-A6 — how did the unit-test job end? Read-only."""
import pathlib
import re

LOG = pathlib.Path(__file__).resolve().parent / "p11b6_unit_log.txt"
_head = LOG.read_bytes()[:4]
_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
lines = [re.sub(r"^.*?\t\S+Z ", "", ln).rstrip()
         for ln in LOG.read_text(encoding=_enc, errors="replace").splitlines()]

print("=" * 78)
print(f"total log lines: {len(lines)}")
print("=" * 78)
print("MARKERS PRESENT?")
for token in ("FAILURES", "short test summary info", "=== ERRORS ===",
              "The operation was canceled", "##[error]", "Process completed with exit code",
              "timed out", "Terminate batch"):
    hits = [i for i, ln in enumerate(lines) if token.lower() in ln.lower()]
    print(f"   {token!r:<42} {len(hits)} hit(s)" + (f"  first at line {hits[0]}" if hits else ""))

print("-" * 78)
print("LAST 30 NON-EMPTY LINES")
tail = [ln for ln in lines if ln.strip()][-30:]
for ln in tail:
    print("   " + ln[:180])

print("-" * 78)
print("OUTCOME TALLY across the whole run (per-test result lines)")
tally = {}
for ln in lines:
    m = re.search(r"\b(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b\s*\[", ln)
    if m:
        tally[m.group(1)] = tally.get(m.group(1), 0) + 1
for k in sorted(tally, key=lambda x: -tally[x]):
    print(f"   {k:<9} {tally[k]}")

print("-" * 78)
print("ALL FAILED/ERROR TEST NODE IDS (from per-test result lines)")
failed = []
for ln in lines:
    m = re.match(r"^(tests/\S+::\S+)\s+(FAILED|ERROR)\b", ln.strip())
    if m:
        failed.append(m.group(1))
seen = []
for f in failed:
    if f not in seen:
        seen.append(f)
print(f"   distinct failing node ids: {len(seen)}")
for f in seen[:60]:
    print("   " + f[:160])
