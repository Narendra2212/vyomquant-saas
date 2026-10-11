"""Phase 9 artifact consistency, as ASSERTIONS with a non-zero exit code.

Same contract as staging/verify_phase8_consistency.py, advanced to Phase 9's state. It
FAILS LOUDLY rather than printing a reconciliation that could scroll past:

  1. Every finding id in any findings array of any of the four JSONs is in the
     authoritative remediation block, under `resolved` or `open`. An id in NEITHER has
     dropped out of the current-state summary.
  2. No id is in BOTH.
  3. Every `resolved` entry carries a non-empty resolution AND non-empty evidence.
  4. Every `open` entry carries a severity and a non-empty detail.
  5. No findings-array item contradicts the block (resolved must not read OPEN/fixed=false,
     and vice versa).
  6. The four launch classifications are present and say what the evidence supports.
  7. If any regression lane reports failures, the counts file must NOT claim green and the
     failures must be named.

PLUS the Phase 9 transition assertions: the ten findings Phase 9 set out to close, and the
new TRADE-12, must now be RESOLVED; and nothing Phase 8 left open that Phase 9 did not
touch may have silently vanished.

Run: .venv\\Scripts\\python.exe staging\\verify_phase9_consistency.py
"""
from __future__ import annotations

import json
import pathlib
import sys

STAGING = pathlib.Path(__file__).resolve().parent
FILES = (
    "full_audit_results.json",
    "marketplace_audit.json",
    "money_flow_audit.json",
    "trading_audit.json",
)

#: Phase 9 closed these ten, plus the new TRADE-12. ML-01 is closed as a false positive;
#: it is still a `resolved` entry (its status text says CLOSED AS A FALSE POSITIVE).
PHASE_9_MUST_BE_RESOLVED = (
    "KILL-02", "KILL-03", "KILL-04", "MONEY-06", "MONEY-07", "FEE-01", "ML-01",
    "TRADE-07", "TRADE-11", "TEST-06", "TRADE-12",
)
#: Findings that stay OPEN after Phase 9 (not in Phase 9's scope). A regression that
#: silently closed one of these would be caught here.
#: TENANT-01, QUOTA-01 and CONN-01 were open at Phase 8 and are NOT in Phase 9's scope, so
#: they must still be open. MONEY-04 is DELIBERATELY NOT here: it was CLOSED in Phase 8 (the
#: arithmetic defect was fixed); its earlier P0-class "carried forward" note predates that
#: closure. A regression that silently closed one of the three below would be caught here.
PHASE_9_MUST_STAY_OPEN = ("TENANT-01", "QUOTA-01", "CONN-01")

REQUIRED_CLASSIFICATIONS = {
    "PAPER_ONLY_LAUNCH": "NO-GO",
    "TRADING_LIVE": "OFF / NOT CERTIFIED",
    "TRADING_PAPER": "NOT CLASSIFIED",
    "PRODUCTION": "NOT CLASSIFIED",
}

failures: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def load(name: str):
    path = STAGING / name
    if not path.exists():
        fail(f"{name}: ABSENT. The four JSON artifacts are part of the deliverable.")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"{name}: does not parse ({type(exc).__name__}: {exc})")
        return None


def collect_ids(node, found: dict) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "remediation":
                continue
            collect_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.setdefault(item["id"], []).append(item)
            collect_ids(item, found)


print("=" * 78)
print("PHASE 9 ARTIFACT CONSISTENCY - ASSERTIONS")
print("=" * 78)

documents = {name: load(name) for name in FILES}

for name, data in documents.items():
    if data is None:
        continue
    block = data.get("remediation")
    if not isinstance(block, dict):
        fail(f"{name}: has no `remediation` block")
        continue

    resolved = block.get("resolved") or {}
    still_open = block.get("open") or {}
    classification = block.get("classification") or {}

    print(f"\n--- {name}")
    print(f"    nonce={block.get('nonce')}  resolved={len(resolved)}  open={len(still_open)}")

    both = sorted(set(resolved) & set(still_open))
    if both:
        fail(f"{name}: {len(both)} id(s) are BOTH resolved and open: {both}")

    for fid, entry in sorted(resolved.items()):
        if not isinstance(entry, dict):
            fail(f"{name}: resolved[{fid}] is not an object")
            continue
        if not str(entry.get("resolution") or "").strip():
            fail(f"{name}: resolved[{fid}] has no resolution text")
        if not str(entry.get("evidence") or "").strip():
            fail(f"{name}: resolved[{fid}] is marked resolved with NO EVIDENCE")

    for fid, entry in sorted(still_open.items()):
        if not isinstance(entry, dict):
            fail(f"{name}: open[{fid}] is not an object")
            continue
        if not str(entry.get("severity") or "").strip():
            fail(f"{name}: open[{fid}] has no severity")
        if not str(entry.get("detail") or "").strip():
            fail(f"{name}: open[{fid}] has no detail")

    # Phase 9 transitions.
    for fid in PHASE_9_MUST_BE_RESOLVED:
        if fid in still_open:
            fail(f"{name}: {fid} was closed in Phase 9 but is still listed as open")
        if fid not in resolved:
            fail(f"{name}: {fid} must be RESOLVED after Phase 9 but is absent from resolved")
    for fid in PHASE_9_MUST_STAY_OPEN:
        if fid in resolved:
            fail(f"{name}: {fid} must stay OPEN after Phase 9 but is listed as resolved")
        if fid not in still_open:
            fail(f"{name}: {fid} must stay OPEN after Phase 9 but is absent from open")

    found: dict = {}
    collect_ids(data, found)
    orphans = sorted(fid for fid in found if fid not in resolved and fid not in still_open)
    if orphans:
        fail(
            f"{name}: {len(orphans)} finding id(s) appear in a findings array but in "
            f"NEITHER block of the current-state summary: {orphans}"
        )
    print(f"    distinct ids in arrays = {len(found)}   orphans = {len(orphans)}")

    for fid, items in sorted(found.items()):
        for item in items:
            status = str(item.get("status") or "").upper()
            fixed = item.get("fixed")
            if fid in resolved:
                if status == "OPEN":
                    fail(f"{name}: {fid} resolved in block but an array item has status=OPEN")
                if fixed is False:
                    fail(f"{name}: {fid} resolved in block but an array item has fixed=false")
            elif fid in still_open:
                if fixed is True:
                    fail(f"{name}: {fid} open in block but an array item has fixed=true")

    for key, expected in REQUIRED_CLASSIFICATIONS.items():
        value = str(classification.get(key) or "")
        if not value:
            fail(f"{name}: classification is missing {key}")
        elif expected.upper() not in value.upper():
            fail(f"{name}: classification[{key}] does not state {expected!r}; it says "
                 f"{value[:90]!r}")

# 7. The regression counts must not claim green while reporting failures.
counts_path = STAGING / "fullreg_phase9_counts.json"
if not counts_path.exists():
    notes.append(
        "fullreg_phase9_counts.json is absent, so the lane figures were not cross-checked "
        "by this run. Run staging/finalize_phase9_counts.py after both lanes finish."
    )
else:
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    lanes = counts.get("lanes", {})
    print("\n--- regression lanes")
    any_failed = False
    for lane_name, lane in lanes.items():
        failed = lane.get("failed")
        print(f"    {lane_name:<12} collected={lane.get('collected')} "
              f"passed={lane.get('passed')} failed={failed} "
              f"skipped={lane.get('skipped')} xfailed={lane.get('xfailed')} "
              f"xpassed={lane.get('xpassed')}")
        if failed:
            any_failed = True
            if not lane.get("failed_ids"):
                fail(f"fullreg_phase9_counts.json: the {lane_name} lane reports {failed} "
                     f"failure(s) but names none of them")
    if any_failed and counts.get("fully_green"):
        fail("fullreg_phase9_counts.json claims fully_green while a lane still reports "
             "failures.")
    if not counts.get("accounting_convention"):
        fail("fullreg_phase9_counts.json records no xfail/xpass accounting convention")

print("\n" + "=" * 78)
for note in notes:
    print(f"NOTE: {note}")
if failures:
    print(f"FAILED {len(failures)} consistency assertion(s):")
    for item in failures:
        print(f"  - {item}")
    print("=" * 78)
    sys.exit(1)

print("ALL CONSISTENCY ASSERTIONS PASSED")
print("=" * 78)
sys.exit(0)
