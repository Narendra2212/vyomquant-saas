"""Phase 8 artifact consistency, as ASSERTIONS with a non-zero exit code.

`staging/verify_audit_json.py` PRINTS a reconciliation and always exits 0, which is useful
for reading but is not a check: a contradiction would scroll past. Phase 8's brief asks for
something stronger - "assert programmatically that no open finding is omitted from the
current-state summary" and "verify resolved findings have evidence and do not retain
contradictory current-state labels" - so this script fails loudly instead.

WHAT IT ENFORCES
  1. Every finding id in any findings array of any of the four JSONs appears in the
     authoritative remediation block, under `resolved` or `open`. An id in NEITHER is a
     finding that has dropped out of the current-state summary.
  2. No id appears in BOTH. A finding cannot be simultaneously closed and open.
  3. Every `resolved` entry carries a non-empty resolution AND non-empty evidence. "FIXED"
     with no evidence is the exact failure mode this audit exists to prevent.
  4. Every `open` entry carries a severity and a non-empty detail.
  5. No findings-array item contradicts the block: an id the block calls resolved must not
     still be labelled OPEN or `fixed: false` in the array, and vice versa.
  6. The four launch classifications are present and say what the evidence supports.
  7. If any regression lane reports failures, the counts file must NOT claim to be green,
     and the failures must be named.

Run: .venv\\Scripts\\python.exe staging\\verify_phase8_consistency.py
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

#: Findings Phase 8 closed, and the new ones it opened. Named explicitly so a regeneration
#: that silently drops one is caught, rather than only the generic sweep running.
PHASE_8_MUST_BE_RESOLVED = (
    "TENANT-02", "MONEY-04", "TRADE-01", "TRADE-02", "KILL-01", "TRADE-10",
)
#: TRADE-07 is pre-existing and stays open (Phase 8 only narrowed it). TRADE-11 is new.
PHASE_8_MUST_BE_OPEN = (
    "MONEY-06", "MONEY-07", "FEE-01", "KILL-02", "KILL-03", "KILL-04", "ML-01",
    "TRADE-07", "TRADE-11", "TEST-06",
)

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
    """Every `id` on any dict inside any list, at any depth, with its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "remediation":
                continue  # the block itself, not a findings array
            collect_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                found.setdefault(item["id"], []).append(item)
            collect_ids(item, found)


print("=" * 78)
print("PHASE 8 ARTIFACT CONSISTENCY - ASSERTIONS")
print("=" * 78)

documents = {name: load(name) for name in FILES}

for name, data in documents.items():
    if data is None:
        continue
    block = data.get("remediation")
    if not isinstance(block, dict):
        fail(f"{name}: has no `remediation` block, so it has no authoritative current state")
        continue

    resolved = block.get("resolved") or {}
    still_open = block.get("open") or {}
    classification = block.get("classification") or {}

    print(f"\n--- {name}")
    print(f"    resolved = {len(resolved)}   open = {len(still_open)}")

    # 2. No id in both.
    both = sorted(set(resolved) & set(still_open))
    if both:
        fail(f"{name}: {len(both)} id(s) are BOTH resolved and open: {both}")

    # 3. Resolved entries must carry resolution and evidence.
    for fid, entry in sorted(resolved.items()):
        if not isinstance(entry, dict):
            fail(f"{name}: resolved[{fid}] is not an object")
            continue
        if not str(entry.get("resolution") or "").strip():
            fail(f"{name}: resolved[{fid}] has no resolution text")
        if not str(entry.get("evidence") or "").strip():
            fail(f"{name}: resolved[{fid}] is marked resolved with NO EVIDENCE")

    # 4. Open entries must carry severity and detail.
    for fid, entry in sorted(still_open.items()):
        if not isinstance(entry, dict):
            fail(f"{name}: open[{fid}] is not an object")
            continue
        if not str(entry.get("severity") or "").strip():
            fail(f"{name}: open[{fid}] has no severity")
        if not str(entry.get("detail") or "").strip():
            fail(f"{name}: open[{fid}] has no detail")

    # Phase 8's own findings must be where Phase 8 says they are.
    for fid in PHASE_8_MUST_BE_RESOLVED:
        if fid in still_open:
            fail(f"{name}: {fid} was closed in Phase 8 but is still listed as open")
    for fid in PHASE_8_MUST_BE_OPEN:
        if fid in resolved:
            fail(f"{name}: {fid} is a Phase 8 OPEN finding but is listed as resolved")

    # 1 + 5. Every id in a findings array is accounted for, and not contradicted.
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
                    fail(
                        f"{name}: {fid} is resolved in the block but an array item still "
                        f"carries status=OPEN"
                    )
                if fixed is False:
                    fail(
                        f"{name}: {fid} is resolved in the block but an array item still "
                        f"carries fixed=false"
                    )
            elif fid in still_open:
                if fixed is True:
                    fail(
                        f"{name}: {fid} is open in the block but an array item carries "
                        f"fixed=true"
                    )

    # 6. The launch classifications.
    for key, expected in REQUIRED_CLASSIFICATIONS.items():
        value = str(classification.get(key) or "")
        if not value:
            fail(f"{name}: classification is missing {key}")
        elif expected.upper() not in value.upper():
            fail(
                f"{name}: classification[{key}] does not state {expected!r}; it says "
                f"{value[:90]!r}"
            )

# 7. The regression counts must not claim green while reporting failures.
counts_path = STAGING / "fullreg_phase8_counts.json"
if not counts_path.exists():
    notes.append(
        "fullreg_phase8_counts.json is absent, so the lane figures were not cross-checked "
        "by this run. Run staging/finalize_phase8_counts.py after both lanes finish."
    )
else:
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    lanes = counts.get("lanes", {})
    print("\n--- regression lanes")
    any_failed = False
    for lane_name, lane in lanes.items():
        failed = lane.get("failed")
        print(
            f"    {lane_name:<12} collected={lane.get('collected')} "
            f"passed={lane.get('passed')} failed={failed} "
            f"skipped={lane.get('skipped')} xfailed={lane.get('xfailed')} "
            f"xpassed={lane.get('xpassed')}"
        )
        if failed:
            any_failed = True
            if not lane.get("failed_ids"):
                fail(
                    f"fullreg_phase8_counts.json: the {lane_name} lane reports "
                    f"{failed} failure(s) but names none of them"
                )
    if any_failed and counts.get("fully_green"):
        fail(
            "fullreg_phase8_counts.json claims fully_green while a lane still reports "
            "failures. A lane must never be presented as green while its reported "
            "failures remain."
        )
    if not counts.get("accounting_convention"):
        fail("fullreg_phase8_counts.json records no xfail/xpass accounting convention")

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
