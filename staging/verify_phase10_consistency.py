"""Phase 10 artifact consistency, as ASSERTIONS with a non-zero exit code.

Same contract as verify_phase9_consistency.py, advanced to Phase 10:
  1-7 as before (every array id is in resolved|open; none in both; resolved carries
  resolution+evidence; open carries severity+detail; no array item contradicts the block;
  the four classifications are present and correct; a lane reporting failures must name them
  and must not claim green).
  PLUS the Phase 10 transitions: CONN-01, FEE-02, MONEY-08, QUOTA-01 must be RESOLVED;
  TENANT-01 and RECON-01 must be OPEN.

Run: .venv\\Scripts\\python.exe staging\\verify_phase10_consistency.py
"""
from __future__ import annotations

import json
import pathlib
import sys

STAGING = pathlib.Path(__file__).resolve().parent
FILES = ("full_audit_results.json", "marketplace_audit.json",
         "money_flow_audit.json", "trading_audit.json")

PHASE_10_MUST_BE_RESOLVED = ("CONN-01", "FEE-02", "MONEY-08", "QUOTA-01")
PHASE_10_MUST_STAY_OPEN = ("TENANT-01", "RECON-01")

REQUIRED_CLASSIFICATIONS = {
    "PAPER_ONLY_LAUNCH": "NO-GO",
    "TRADING_LIVE": "OFF / NOT CERTIFIED",
    "TRADING_PAPER": "NOT CLASSIFIED",
    "PRODUCTION": "NOT CLASSIFIED",
}

failures: list[str] = []
notes: list[str] = []


def fail(m): failures.append(m)


def load(name):
    p = STAGING / name
    if not p.exists():
        fail(f"{name}: ABSENT.")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"{name}: does not parse ({type(exc).__name__}: {exc})")
        return None


def collect_ids(node, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "remediation":
                continue
            collect_ids(v, found)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.setdefault(item["id"], []).append(item)
            collect_ids(item, found)


print("=" * 78)
print("PHASE 10 ARTIFACT CONSISTENCY - ASSERTIONS")
print("=" * 78)

for name in FILES:
    data = load(name)
    if data is None:
        continue
    block = data.get("remediation")
    if not isinstance(block, dict):
        fail(f"{name}: has no remediation block")
        continue
    resolved = block.get("resolved") or {}
    still_open = block.get("open") or {}
    classification = block.get("classification") or {}

    print(f"\n--- {name}  nonce={block.get('nonce')}  resolved={len(resolved)}  open={len(still_open)}")

    both = sorted(set(resolved) & set(still_open))
    if both:
        fail(f"{name}: ids BOTH resolved and open: {both}")

    for fid, e in sorted(resolved.items()):
        if not isinstance(e, dict):
            fail(f"{name}: resolved[{fid}] not an object"); continue
        if not str(e.get("resolution") or "").strip():
            fail(f"{name}: resolved[{fid}] has no resolution")
        if not str(e.get("evidence") or "").strip():
            fail(f"{name}: resolved[{fid}] has NO EVIDENCE")
    for fid, e in sorted(still_open.items()):
        if not isinstance(e, dict):
            fail(f"{name}: open[{fid}] not an object"); continue
        if not str(e.get("severity") or "").strip():
            fail(f"{name}: open[{fid}] has no severity")
        if not str(e.get("detail") or "").strip():
            fail(f"{name}: open[{fid}] has no detail")

    for fid in PHASE_10_MUST_BE_RESOLVED:
        if fid in still_open:
            fail(f"{name}: {fid} closed in Phase 10 but still open")
        if fid not in resolved:
            fail(f"{name}: {fid} must be RESOLVED after Phase 10 but absent from resolved")
    for fid in PHASE_10_MUST_STAY_OPEN:
        if fid in resolved:
            fail(f"{name}: {fid} must stay OPEN after Phase 10 but is resolved")
        if fid not in still_open:
            fail(f"{name}: {fid} must stay OPEN after Phase 10 but absent from open")

    found = {}
    collect_ids(data, found)
    orphans = sorted(f for f in found if f not in resolved and f not in still_open)
    if orphans:
        fail(f"{name}: {len(orphans)} id(s) in a findings array but in NEITHER block: {orphans}")
    print(f"    distinct ids in arrays = {len(found)}   orphans = {len(orphans)}")

    for fid, items in sorted(found.items()):
        for item in items:
            status = str(item.get("status") or "").upper()
            fixed = item.get("fixed")
            if fid in resolved:
                if status == "OPEN":
                    fail(f"{name}: {fid} resolved in block but array item status=OPEN")
                if fixed is False:
                    fail(f"{name}: {fid} resolved in block but array item fixed=false")
            elif fid in still_open and fixed is True:
                fail(f"{name}: {fid} open in block but array item fixed=true")

    for key, expected in REQUIRED_CLASSIFICATIONS.items():
        value = str(classification.get(key) or "")
        if not value:
            fail(f"{name}: classification missing {key}")
        elif expected.upper() not in value.upper():
            fail(f"{name}: classification[{key}] does not state {expected!r}: {value[:80]!r}")

counts_path = STAGING / "fullreg_phase10_counts.json"
if not counts_path.exists():
    notes.append("fullreg_phase10_counts.json absent; run staging/finalize_phase10_counts.py "
                 "after both lanes finish.")
else:
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    lanes = counts.get("lanes", {})
    print("\n--- regression lanes")
    any_failed = False
    for lane_name, lane in lanes.items():
        failed = lane.get("failed")
        print(f"    {lane_name:<12} collected={lane.get('collected')} passed={lane.get('passed')} "
              f"failed={failed} skipped={lane.get('skipped')} xfailed={lane.get('xfailed')} "
              f"xpassed={lane.get('xpassed')}")
        if failed:
            any_failed = True
            if not lane.get("failed_ids"):
                fail(f"fullreg_phase10_counts.json: {lane_name} reports {failed} failure(s), names none")
    if any_failed and counts.get("fully_green"):
        fail("fullreg_phase10_counts.json claims fully_green while a lane reports failures")
    if not counts.get("accounting_convention"):
        fail("fullreg_phase10_counts.json records no xfail/xpass accounting convention")

print("\n" + "=" * 78)
for n in notes:
    print(f"NOTE: {n}")
if failures:
    print(f"FAILED {len(failures)} consistency assertion(s):")
    for item in failures:
        print(f"  - {item}")
    print("=" * 78)
    sys.exit(1)
print("ALL CONSISTENCY ASSERTIONS PASSED")
print("=" * 78)
sys.exit(0)
