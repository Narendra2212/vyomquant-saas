"""Final read-back of what the four JSON artifacts now assert. Reporting only."""
from __future__ import annotations

import json
import pathlib

STAGING = pathlib.Path(__file__).resolve().parent
FILES = ("full_audit_results.json", "marketplace_audit.json",
         "money_flow_audit.json", "trading_audit.json")

for name in FILES:
    d = json.loads((STAGING / name).read_text(encoding="utf-8"))
    rem = d["remediation"]
    print(f"{name:<28} nonce={rem['nonce']}  resolved={len(rem['resolved'])} "
          f"open={len(rem['open'])} corrections={len(rem['corrections'])}")

d = json.loads((STAGING / "full_audit_results.json").read_text(encoding="utf-8"))
rem = d["remediation"]
te = rem["test_evidence"]
fr = te.get("full_regression_phase_8", {})

print("\nPHASE 8 REGRESSION BLOCK PRESENT =", bool(fr))
for lane, v in fr.get("lanes", {}).items():
    print(f"  {lane:<12} collected={v.get('collected')} passed={v.get('passed')} "
          f"failed={v.get('failed')} errors={v.get('errors')} "
          f"skipped={v.get('skipped')} xfailed={v.get('xfailed')} "
          f"xpassed={v.get('xpassed')} dur={v.get('duration_seconds')}s")
    for check, ok in (v.get("junit_cross_check") or {}).items():
        if ok is False:
            print(f"      CROSS-CHECK FAILED: {check}")
print("  fully_green =", fr.get("fully_green"))
print("  intermediate lanes =", list(fr.get("intermediate_lanes", {}).keys()))
print("  accounting convention recorded =", bool(fr.get("accounting_convention")))

print("\nCLASSIFICATION")
for key in ("PAPER_ONLY_LAUNCH", "TRADING_LIVE", "TRADING_PAPER", "PRODUCTION"):
    value = rem["classification"].get(key, "<MISSING>")
    first = value.split(".")[0]
    print(f"  {key:<18} {first}")

print("\nPHASE 8 OPEN FINDINGS IN THE AUTHORITATIVE BLOCK")
phase8 = ("KILL-02", "KILL-03", "KILL-04", "MONEY-06", "MONEY-07", "FEE-01",
          "ML-01", "TRADE-07", "TRADE-11", "TEST-06")
for fid in phase8:
    entry = rem["open"].get(fid)
    mark = "OPEN  " if entry else "ABSENT"
    sev = entry["severity"] if entry else "-"
    print(f"  {fid:<10} {mark} sev={sev}")

print("\nPHASE 8 RESOLVED, EVIDENCE PRESENT?")
for fid in ("TENANT-02", "MONEY-04", "TRADE-01", "TRADE-02", "KILL-01", "TRADE-10"):
    entry = rem["resolved"].get(fid)
    if not entry:
        print(f"  {fid:<10} ABSENT")
        continue
    print(f"  {fid:<10} {entry['status']:<6} evidence_chars={len(entry.get('evidence') or '')}")

print("\ndispositions block present =", "phase_8_dispositions" in te)
