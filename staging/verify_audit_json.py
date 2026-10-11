"""Confirm the regenerated JSON artifacts actually say what the scorecard says.

Reads each file back from disk and reports, per finding id, what status is recorded --
both in any findings array and in the `remediation` block. Written because "the script
printed UPDATED" is not evidence that a particular finding's status changed.
"""
from __future__ import annotations

import json
from pathlib import Path

STAGING = Path(__file__).resolve().parent
FILES = ["full_audit_results.json", "marketplace_audit.json",
         "money_flow_audit.json", "trading_audit.json"]

WATCH = ["WORK-03", "OBS-01", "API-04", "QUOTA-01", "TENANT-01", "TRADE-04",
         "TRADE-03", "PAPER-01", "AUTH-03",
         # Phase 6. PAPER-01 was already watched, which is the point of watching it:
         # its status should now read RESOLVED rather than OPEN.
         "PAPER-02", "PAPER-03", "TEST-01", "TEST-02", "TEST-04", "TEST-05",
         # Phase 7. PAPER-02, LIB-01, ENV-01, ENV-03, ENV-04, ENV-06 and API-05 were
         # already watched as OPEN; they must now read RESOLVED, which is exactly what
         # watching them is for. The five new ids must be PRESENT and OPEN - a new finding
         # that silently fails to reach either block is the failure mode this file exists
         # to catch.
         "LIB-02", "LIB-03", "LIB-04", "PAPER-04", "PAPER-05", "CONN-01",
         "DEADVALIDATOR-02", "TENANT-02",
         # Added to this block in Phase 7 after the general sweep below found them missing
         # from the authoritative record entirely. TRADE-01 and TRADE-02 are P1.
         "TRADE-01", "TRADE-02", "TRADE-07", "TRADE-08", "TRADE-09",
         "MKT-01", "MKT-02", "MONEY-03",
         "ENV-02", "ENV-03", "ENV-04", "ENV-05", "ENV-06", "TRADE-06", "MONEY-04",
         "SAST-01", "ENV-01", "ENV-02", "ENV-03", "ENV-04", "ENV-05", "ENV-06",
         "API-05", "LIB-01"]

print("PER-FILE FINDINGS ARRAYS")
print("=" * 74)
for name in FILES:
    path = STAGING / name
    data = json.loads(path.read_text(encoding="utf-8"))
    hits = []
    for key, value in data.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("id") in WATCH:
                hits.append((item["id"], key, item.get("status"),
                             item.get("fixed")))
    print(f"\n{name}")
    if not hits:
        print("  (no watched findings in any array)")
    for fid, arr, status, fixed in sorted(hits):
        print(f"  {fid:<12} [{arr}] status={status!r} fixed={fixed}")

print("\n" + "=" * 74)
print("REMEDIATION BLOCK (authoritative, written by update_audit_json.py)")
print("=" * 74)
data = json.loads((STAGING / "full_audit_results.json").read_text(encoding="utf-8"))
rem = data.get("remediation", {})
resolved = rem.get("resolved", {})
still = rem.get("still_open", rem.get("open", {}))
print(f"nonce            = {rem.get('nonce')}")
print(f"resolved count   = {len(resolved)}")
print(f"still_open count = {len(still)}")
for fid in WATCH:
    if fid in resolved:
        print(f"  {fid:<12} RESOLVED status={resolved[fid].get('status')!r}")
    elif fid in still:
        entry = still[fid]
        sev = entry.get("severity") if isinstance(entry, dict) else entry
        print(f"  {fid:<12} OPEN severity={sev!r}")
    else:
        print(f"  {fid:<12} *** NOT PRESENT IN EITHER BLOCK ***")

# ──────────────────────────────────────────────────────────────────────────────────────────
#  THE GENERAL VERSION OF THE SAME CHECK
# ──────────────────────────────────────────────────────────────────────────────────────────
# WATCH is a hand-maintained list, so it can only catch a gap somebody remembered to watch.
# In Phase 7 it caught TRADE-06 and MONEY-04 missing from the remediation block entirely -
# both genuinely open, both present in the per-file `defects` arrays, and neither listed in
# the authoritative current-state record. A current open finding that is invisible in the
# current-state record is the exact failure mode the Phase 7 brief forbids.
#
# This sweeps EVERY id in EVERY findings array across all four files, so the next such gap
# does not depend on anybody having added the id to WATCH first.
print("\n" + "=" * 74)
print("EVERY FINDING ID IN ANY ARRAY, CHECKED AGAINST THE REMEDIATION BLOCK")
print("=" * 74)

all_ids: dict[str, set[str]] = {}
for name in FILES:
    path = STAGING / name
    if not path.exists():
        continue
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        continue
    for key, value in doc.items():
        if key == "remediation" or not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                all_ids.setdefault(item["id"], set()).add(f"{name}:{key}")

orphans = sorted(fid for fid in all_ids if fid not in resolved and fid not in still)
print(f"distinct ids found in arrays = {len(all_ids)}")
print(f"ids in NEITHER block         = {len(orphans)}")
for fid in orphans:
    print(f"  *** {fid:<24} present in {', '.join(sorted(all_ids[fid]))}")
if not orphans:
    print("  (none - every finding in every array is accounted for in the "
          "remediation block)")

corrections = rem.get("corrections", data.get("corrections", []))
print(f"\ncorrections recorded = {len(corrections)}")
for c in corrections:
    print(f"  {c.get('id')}: {str(c.get('withdrawn_claim'))[:68]}...")

cls = rem.get("classification", data.get("classification", {}))
print("\nCLASSIFICATION")
for k, v in cls.items():
    if isinstance(v, list):
        print(f"  {k}:")
        for item in v:
            print(f"    - {str(item)[:90]}")
    else:
        print(f"  {k}: {v}")

evidence = rem.get("test_evidence", data.get("test_evidence", {}))

print("\nPHASE 7 FULL REGRESSION BLOCK (both lanes)")
p7 = evidence.get("full_regression_phase_7", {})
if not p7:
    print("  *** ABSENT ***")
for key, value in p7.items():
    if key == "lanes" and isinstance(value, dict):
        for lane_name, lane in value.items():
            print(f"  {lane_name}:")
            for field in (
                "status", "collected", "passed", "failed", "errors", "skipped",
                "xfailed", "xpassed", "duration_seconds",
            ):
                print(f"    {field}: {lane.get(field)}")
            checks = lane.get("junit_cross_check") or {}
            bad = [name for name, ok in checks.items() if not ok]
            print(f"    junit_cross_check: {len(checks)} checks, "
                  f"{'ALL OK' if checks and not bad else ('FAILING: ' + ', '.join(bad)) if bad else 'NONE RECORDED'}")
    else:
        print(f"  {key}: {str(value)[:140]}")

fr = evidence.get("full_regression", {})
print("\nFULL REGRESSION BLOCK (earlier phases)")
for k, v in fr.items():
    print(f"  {k}: {str(v)[:110]}")
